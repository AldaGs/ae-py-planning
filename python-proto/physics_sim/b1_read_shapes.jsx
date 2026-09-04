/*
 * B1 -- read the active comp's shape layers, write ae-physics-scene/1.
 *
 * Run:  File > Scripts > Run Script File...   (or drop into ScriptUI Panels)
 * Out:  a .json next to the project, chosen by a save dialog.
 *
 * WHAT THIS SCRIPT OWES THE PYTHON SIDE
 * -------------------------------------
 * One promise, and it is the whole reason this file is not trivial:
 *
 *     PATH VERTICES COME OUT IN LAYER SPACE.
 *
 * They do not start there. A shape layer is a tree -- Contents holds groups,
 * groups hold groups, and every group has its own Transform (anchor, position,
 * scale, rotation, skew). A path's vertices are in the space of whatever group
 * encloses it. A3 assumed layer space and flagged it as B1's to confirm; the
 * answer is that the assumption is false in AE, so this script composes the
 * group transforms down and hands over the result. Complexity at the boundary.
 *
 * ES3 NOTES  (ExtendScript is not modern JavaScript)
 * --------------------------------------------------
 *   - There is no JSON object. The serialiser at the bottom is hand-rolled.
 *   - There is no Array.forEach/map/indexOf, no let/const, no arrow functions,
 *     no trailing commas in literals.
 *   - Number.toFixed exists, and is how precision gets bounded.
 *   - String concatenation in a loop is O(n^2) in some hosts; vertices go
 *     through an array + join instead.
 *
 * UNVERIFIED UNTIL RUN IN AE. Everything on the Python side of B1 is tested
 * offline against a fixture; this half cannot be, and the first real run is
 * expected to find something. `scene_io.validate()` exists to make whatever it
 * finds legible rather than a stack trace.
 */

(function () {

var SCHEMA = "ae-physics-scene/1";
var PRECISION = 4;          // px, well under the 1.0 simplify tolerance
var WARNINGS = [];

function warn(s) { WARNINGS.push(s); }

// -------------------------------------------------------------------------
// Transform composition -- mirrors scene_io.compose_point exactly
// -------------------------------------------------------------------------

function radians(d) { return d * Math.PI / 180.0; }

/* parent = position + R(rot) * Skew * S(scale) * (p - anchor) */
function composePoint(p, t) {
    var x = (p[0] - t.anchor[0]) * t.scale[0] / 100.0;
    var y = (p[1] - t.anchor[1]) * t.scale[1] / 100.0;
    if (t.skew) {
        var a = radians(t.skewAxis);
        var ca = Math.cos(a), sa = Math.sin(a);
        var u = ca * x + sa * y, v = -sa * x + ca * y;
        u += Math.tan(radians(t.skew)) * v;
        x = ca * u - sa * v;
        y = sa * u + ca * v;
    }
    var r = radians(t.rotation);
    var c = Math.cos(r), s = Math.sin(r);
    return [t.position[0] + x * c - y * s, t.position[1] + x * s + y * c];
}

/* Tangents are stored RELATIVE to their vertex, so they must move as vectors.
 * Rather than write a second, translation-free matrix that could drift from
 * composePoint, transform the absolute control point and subtract. */
function composeShape(shape, t) {
    var i, n = shape.vertices.length;
    var v = [], it = [], ot = [];
    for (i = 0; i < n; i++) v.push(composePoint(shape.vertices[i], t));
    for (i = 0; i < n; i++) {
        var av = shape.vertices[i];
        var ai = composePoint([av[0] + shape.inTangents[i][0],
                               av[1] + shape.inTangents[i][1]], t);
        var ao = composePoint([av[0] + shape.outTangents[i][0],
                               av[1] + shape.outTangents[i][1]], t);
        it.push([ai[0] - v[i][0], ai[1] - v[i][1]]);
        ot.push([ao[0] - v[i][0], ao[1] - v[i][1]]);
    }
    return {vertices: v, inTangents: it, outTangents: ot,
            closed: shape.closed};
}

function identity() {
    return {anchor: [0, 0], position: [0, 0], scale: [100, 100],
            rotation: 0, skew: 0, skewAxis: 0};
}

/* Compose child into parent: apply child first, then parent. Rather than
 * multiply matrices, the caller keeps a stack and applies each shape through
 * every transform from its own group outwards -- fewer places to be wrong,
 * and paths are few. */
function applyStack(shape, stack) {
    var out = shape;
    for (var i = stack.length - 1; i >= 0; i--) out = composeShape(out, stack[i]);
    return out;
}

// -------------------------------------------------------------------------
// Reading a group's transform
// -------------------------------------------------------------------------

function propVal(group, matchName, fallback) {
    var p = null;
    try { p = group.property(matchName); } catch (e) { p = null; }
    if (p === null) return fallback;
    var v = p.valueAtTime(0, false);
    if (p.numKeys > 0) {
        warn("'" + group.name + "' has an animated " + p.name +
             "; the value at time 0 is used and the animation ignored");
    }
    return v;
}

function groupTransform(vectorGroup) {
    var tg = null;
    try { tg = vectorGroup.property("ADBE Vector Transform Group"); }
    catch (e) { return identity(); }
    if (tg === null) return identity();

    var t = identity();
    var a = propVal(tg, "ADBE Vector Anchor", [0, 0]);
    var p = propVal(tg, "ADBE Vector Position", [0, 0]);
    var s = propVal(tg, "ADBE Vector Scale", [100, 100]);
    t.anchor = [a[0], a[1]];
    t.position = [p[0], p[1]];
    t.scale = [s[0], s[1]];
    t.rotation = propVal(tg, "ADBE Vector Rotation", 0);
    t.skew = propVal(tg, "ADBE Vector Skew", 0);
    t.skewAxis = propVal(tg, "ADBE Vector Skew Axis", 0);
    if (t.skew) {
        warn("group '" + vectorGroup.name + "' has skew " + t.skew +
             "; it is composed into the geometry, but a skewed body is not " +
             "something the solver can reproduce as a rigid transform");
    }
    return t;
}

// -------------------------------------------------------------------------
// Walking Contents for paths
// -------------------------------------------------------------------------

/* Collects every "ADBE Vector Shape" under `group`, each already composed out
 * to layer space by the transforms enclosing it.
 *
 * Parametric shapes -- Rectangle, Ellipse, Polystar -- have NO vertex list.
 * They are "ADBE Vector Shape - Rect" and friends, and there is no scripting
 * API that converts one to a path. AE's own right-click does it by hand. So
 * they are reported, loudly, rather than silently contributing nothing. */
function collectPaths(group, stack, out) {
    var i, n = group.numProperties;
    for (i = 1; i <= n; i++) {
        var pr = group.property(i);
        var mn = pr.matchName;

        if (mn === "ADBE Vector Group") {
            var sub = null;
            try { sub = pr.property("ADBE Vectors Group"); } catch (e) {}
            if (sub !== null) {
                stack.push(groupTransform(pr));
                collectPaths(sub, stack, out);
                stack.pop();
            }
        } else if (mn === "ADBE Vector Shape - Group") {
            var sp = pr.property("ADBE Vector Shape");
            if (sp.numKeys > 0) {
                warn("path in '" + pr.name + "' is animated; the shape at " +
                     "time 0 is used");
            }
            var sh = sp.valueAtTime(0, false);
            if (!sh.closed) {
                warn("path in '" + pr.name + "' is OPEN and was skipped; a " +
                     "collision shape must be closed");
                continue;
            }
            out.push(applyStack({vertices: sh.vertices,
                                 inTangents: sh.inTangents,
                                 outTangents: sh.outTangents,
                                 closed: true}, stack));
        } else if (mn === "ADBE Vector Shape - Rect" ||
                   mn === "ADBE Vector Shape - Ellipse" ||
                   mn === "ADBE Vector Shape - Star") {
            warn("'" + pr.name + "' is a PARAMETRIC shape (rectangle, " +
                 "ellipse or polystar). Script cannot read vertices from one " +
                 "-- right-click the shape in AE and choose 'Convert To " +
                 "Bezier Path', then run this again.");
        }
    }
}

// -------------------------------------------------------------------------
// Layers
// -------------------------------------------------------------------------

function layerTransformValue(layer, matchName, fallback) {
    var p = layer.property("ADBE Transform Group").property(matchName);
    if (p === null) return fallback;
    return p.valueAtTime(0, false);
}

function readLayer(layer) {
    if (!(layer instanceof ShapeLayer)) return null;
    if (layer.threeDLayer) {
        warn("layer " + layer.index + " ('" + layer.name + "') is 3D and was " +
             "skipped; the sandbox is a 2D solver");
        return null;
    }

    var paths = [];
    var contents = layer.property("ADBE Root Vectors Group");
    collectPaths(contents, [], paths);
    if (paths.length === 0) {
        warn("layer " + layer.index + " ('" + layer.name + "') contributed no " +
             "closed bezier paths and was skipped");
        return null;
    }

    var a = layerTransformValue(layer, "ADBE Anchor Point", [0, 0, 0]);
    var p = layerTransformValue(layer, "ADBE Position", [0, 0, 0]);
    var s = layerTransformValue(layer, "ADBE Scale", [100, 100, 100]);
    var r = layerTransformValue(layer, "ADBE Rotate Z", 0);

    var scaleProp = layer.property("ADBE Transform Group")
                         .property("ADBE Scale");
    var scaleAnimated = (scaleProp !== null && scaleProp.numKeys > 0);

    var posProp = layer.property("ADBE Transform Group")
                       .property("ADBE Position");
    if (posProp !== null && posProp.numKeys > 0) {
        warn("layer " + layer.index + " ('" + layer.name + "') already has " +
             "Position keyframes. The sim starts from the value at time 0, " +
             "and applying a bake will replace them.");
    }

    return {
        id: layer.index,
        name: layer.name,
        anchor: [a[0], a[1]],
        position: [p[0], p[1]],
        rotation_deg: r,
        scale: [s[0], s[1]],
        scale_animated: scaleAnimated,
        paths: paths
    };
}

// -------------------------------------------------------------------------
// Serialising  (there is no JSON object in ExtendScript)
// -------------------------------------------------------------------------

function num(x) {
    if (typeof x !== "number" || isNaN(x) || !isFinite(x)) return "null";
    var s = x.toFixed(PRECISION);
    // Trim trailing zeros so the file is not mostly ".0000".
    s = s.replace(/\.?0+$/, "");
    return (s === "" || s === "-") ? "0" : s;
}

function vec(v) { return "[" + num(v[0]) + "," + num(v[1]) + "]"; }

function vecList(list) {
    var parts = [];
    for (var i = 0; i < list.length; i++) parts.push(vec(list[i]));
    return "[" + parts.join(",") + "]";
}

function str(s) {
    var out = "", i, c;
    for (i = 0; i < s.length; i++) {
        c = s.charAt(i);
        if (c === '"' || c === "\\") out += "\\" + c;
        else if (c === "\n") out += "\\n";
        else if (c === "\r") out += "\\r";
        else if (c === "\t") out += "\\t";
        else if (c < " ") out += "\\u" + ("000" +
                 s.charCodeAt(i).toString(16)).slice(-4);
        else out += c;
    }
    return '"' + out + '"';
}

function pathJson(sh) {
    return "{\"vertices\":" + vecList(sh.vertices) +
           ",\"inTangents\":" + vecList(sh.inTangents) +
           ",\"outTangents\":" + vecList(sh.outTangents) +
           ",\"closed\":true}";
}

function layerJson(L) {
    var ps = [];
    for (var i = 0; i < L.paths.length; i++) ps.push(pathJson(L.paths[i]));
    return "{\"id\":" + L.id +
           ",\"name\":" + str(L.name) +
           ",\"anchor\":" + vec(L.anchor) +
           ",\"position\":" + vec(L.position) +
           ",\"rotation_deg\":" + num(L.rotation_deg) +
           ",\"scale\":" + vec(L.scale) +
           ",\"scale_animated\":" + (L.scale_animated ? "true" : "false") +
           ",\"paths\":[" + ps.join(",") + "]}";
}

// -------------------------------------------------------------------------
// Main
// -------------------------------------------------------------------------

var comp = app.project.activeItem;
if (!(comp instanceof CompItem)) {
    alert("Select a composition first (click it in the Project panel).");
    return;
}

var layers = [];
for (var i = 1; i <= comp.numLayers; i++) {
    var L = readLayer(comp.layer(i));
    if (L !== null) layers.push(L);
}

if (layers.length === 0) {
    alert("No usable shape layers found in '" + comp.name + "'.\n\n" +
          WARNINGS.join("\n\n"));
    return;
}

var durationFrames = Math.round(comp.duration * comp.frameRate);

var chunks = [];
chunks.push("{\"schema\":" + str(SCHEMA));
chunks.push(",\"comp\":{\"name\":" + str(comp.name) +
            ",\"width\":" + comp.width +
            ",\"height\":" + comp.height +
            ",\"fps\":" + num(comp.frameRate) +
            ",\"duration_frames\":" + durationFrames + "}");
chunks.push(",\"sim\":{\"pixels_per_meter\":100,\"gravity_m_s2\":9.8," +
            "\"substeps\":8}");

var ls = [];
for (i = 0; i < layers.length; i++) ls.push(layerJson(layers[i]));
chunks.push(",\"layers\":[" + ls.join(",") + "]");

/* A floor across the bottom of the comp, so a freshly read scene falls onto
 * something instead of out of frame. The panel will own this later. */
chunks.push(",\"statics\":[[[0," + (comp.height - 1) + "],[" + comp.width +
            "," + (comp.height - 1) + "]]]");

var ws = [];
for (i = 0; i < WARNINGS.length; i++) ws.push(str(WARNINGS[i]));
chunks.push(",\"warnings\":[" + ws.join(",") + "]}");

var f = File.saveDialog("Save scene JSON", "*.json");
if (f === null) return;
if (f.name.indexOf(".json") === -1) f = new File(f.fsName + ".json");
f.encoding = "UTF-8";
f.open("w");
f.write(chunks.join(""));
f.close();

alert("Wrote " + layers.length + " layer(s) to\n" + f.fsName +
      (WARNINGS.length ? "\n\n" + WARNINGS.length + " warning(s):\n\n" +
       WARNINGS.join("\n\n") : ""));

})();
