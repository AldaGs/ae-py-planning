/*
 * B2 -- apply an ae-physics-bake/2 to the layers it was baked from, then
 * measure what AE actually stored.
 *
 * Run:  File > Scripts > Run Script File...
 *       pick the bake JSON, then choose where to save the report.
 *
 * WHY IT WRITES A REPORT
 * ----------------------
 * "It looked right" is not a measurement, and this is the one step where the
 * sandbox cannot see anything. So after writing the keyframes the script reads
 * them back OUT of AE and saves what it found. Python then compares that
 * against the bake it asked for. The loop closes with numbers.
 *
 * The read-back samples two different things, and the second one is the point:
 *
 *   stored[]   keyValue(i) at sampled keys -- did AE store what we sent?
 *   tween[]    valueAtTime(frame + 0.5) -- does AE INTERPOLATE the way the
 *              bake assumes? A5 established that a bad bake hides between
 *              keyframes and is invisible on every keyframed frame. Reading
 *              only stored values would repeat that mistake inside AE.
 *
 * WALL I
 * ------
 * The plan has wanted a number for keyframe write cost since before Phase A.
 * Both write paths are timed on every layer: the naive setValueAtTime loop and
 * the bulk setValuesAtTimes. Setting interpolation type is timed separately,
 * because it is per-key too and may well cost more than the values did.
 *
 * INTERPOLATION IS NOT OPTIONAL
 * -----------------------------
 * AE's default for a new Position keyframe is auto-bezier with SPATIAL
 * tangents -- the motion path bows between keys. Our bake is a sampled
 * trajectory that assumes straight lines between samples. Leaving the default
 * would silently add curvature we never simulated, so every key is forced to
 * LINEAR and Position's spatial tangents are zeroed.
 */

(function () {

var BAKE_SCHEMA = "ae-physics-bake/2";
var SAMPLE_STRIDE = 17;     // coprime with everything, so samples do not land
                            // on a pattern; keeps the report small
var MEASURE_BOTH = true;    // time the naive path as well as the bulk one

// -------------------------------------------------------------------------
// Minimal JSON reader (ExtendScript is ES3 -- there is no JSON object)
// -------------------------------------------------------------------------

function parseJson(text) {
    // eval is the pragmatic choice here: the input is a file the user just
    // picked, in a script they ran deliberately. Wrapped in parens so a
    // leading '{' is read as an object literal and not a block.
    return eval("(" + text + ")");
}

function readFile(f) {
    f.encoding = "UTF-8";
    f.open("r");
    var t = f.read();
    f.close();
    return t;
}

// -------------------------------------------------------------------------
// Serialising (same hand-rolled writer as B1)
// -------------------------------------------------------------------------

function num(x) {
    if (typeof x !== "number" || isNaN(x) || !isFinite(x)) return "null";
    var s = x.toFixed(6).replace(/\.?0+$/, "");
    return (s === "" || s === "-") ? "0" : s;
}

function str(s) {
    var out = "", i, c;
    for (i = 0; i < s.length; i++) {
        c = s.charAt(i);
        if (c === '"' || c === "\\") out += "\\" + c;
        else if (c === "\n") out += "\\n";
        else if (c < " ") out += " ";
        else out += c;
    }
    return '"' + out + '"';
}

// -------------------------------------------------------------------------
// Applying
// -------------------------------------------------------------------------

function clearKeys(prop) {
    while (prop.numKeys > 0) prop.removeKey(1);
}

function now() { return (new Date()).getTime(); }

/* Force straight lines between our samples. Without this AE gives a new
 * Position key auto-bezier SPATIAL tangents and bows the motion path between
 * keyframes -- curvature that was never simulated. */
function makeLinear(prop, isSpatial) {
    var lin = KeyframeInterpolationType.LINEAR;
    for (var i = 1; i <= prop.numKeys; i++) {
        prop.setInterpolationTypeAtKey(i, lin, lin);
        if (isSpatial) {
            prop.setSpatialAutoBezierAtKey(i, false);
            prop.setSpatialTangentsAtKey(i, [0, 0], [0, 0]);
        }
    }
}

function applyLoop(prop, times, values) {
    for (var i = 0; i < times.length; i++) {
        prop.setValueAtTime(times[i], values[i]);
    }
}

// -------------------------------------------------------------------------
// Main
// -------------------------------------------------------------------------

var comp = app.project.activeItem;
if (!(comp instanceof CompItem)) {
    alert("Select the composition the bake came from.");
    return;
}

var bf = File.openDialog("Choose the bake JSON", "*.json");
if (bf === null) return;
var bake = parseJson(readFile(bf));

if (bake.schema !== BAKE_SCHEMA) {
    alert("That file says schema " + bake.schema + ", this script applies " +
          BAKE_SCHEMA + ".");
    return;
}

/* Match by id, which is the AE layer index -- and INDEX IS POSITIONAL. Adding,
 * deleting or reordering a layer between the read and the apply silently moves
 * every id after it. So the name is checked too: it is not a key, it is a
 * tripwire, and a mismatch aborts rather than writing keyframes onto whatever
 * happens to be sitting at that index now. */
var i, j, mismatches = [];
for (i = 0; i < bake.layers.length; i++) {
    var bl = bake.layers[i];
    if (bl.id < 1 || bl.id > comp.numLayers) {
        mismatches.push("id " + bl.id + " (" + bl.name +
                        ") is outside this comp, which has " +
                        comp.numLayers + " layers");
    } else if (comp.layer(bl.id).name !== bl.name) {
        mismatches.push("id " + bl.id + ": bake says '" + bl.name +
                        "', comp has '" + comp.layer(bl.id).name + "'");
    }
}
if (mismatches.length) {
    alert("The comp does not match the bake -- nothing was written.\n\n" +
          mismatches.join("\n") +
          "\n\nLayer index is positional. Re-run the reader if you have " +
          "added, deleted or reordered layers since.");
    return;
}

var fps = comp.frameRate;
var frameDur = comp.frameDuration;
var report = {layers: [], timings: []};

app.beginUndoGroup("Apply physics bake");

var tStart = now();
for (i = 0; i < bake.layers.length; i++) {
    var L = bake.layers[i];
    var layer = comp.layer(L.id);
    var pos = layer.property("ADBE Transform Group").property("ADBE Position");
    var rot = layer.property("ADBE Transform Group").property("ADBE Rotate Z");

    var pk = L.keyframes.position, rk = L.keyframes.rotation;
    var pTimes = [], pVals = [], rTimes = [], rVals = [];
    for (j = 0; j < pk.length; j++) {
        pTimes.push(pk[j][0] * frameDur);
        pVals.push([pk[j][1][0], pk[j][1][1]]);
    }
    for (j = 0; j < rk.length; j++) {
        rTimes.push(rk[j][0] * frameDur);
        rVals.push(rk[j][1]);
    }

    var tLoop = -1;
    if (MEASURE_BOTH) {
        clearKeys(pos);
        var t0 = now();
        applyLoop(pos, pTimes, pVals);
        tLoop = now() - t0;
    }

    clearKeys(pos);
    clearKeys(rot);
    var t1 = now();
    pos.setValuesAtTimes(pTimes, pVals);
    rot.setValuesAtTimes(rTimes, rVals);
    var tBulk = now() - t1;

    var t2 = now();
    makeLinear(pos, true);
    makeLinear(rot, false);
    var tInterp = now() - t2;

    report.timings.push({
        id: L.id, name: L.name, keys: pk.length + rk.length,
        loop_ms: tLoop, bulk_ms: tBulk, interp_ms: tInterp
    });

    /* Read back. `stored` is what AE kept; `tween` is what AE will actually
     * draw between our keys, which is the half a still frame cannot show. */
    var stored = [], tween = [];
    for (j = 0; j < pk.length; j += SAMPLE_STRIDE) {
        var f = pk[j][0];
        stored.push([f, pos.valueAtTime(f * frameDur, false)[0],
                        pos.valueAtTime(f * frameDur, false)[1],
                        rot.valueAtTime(f * frameDur, false)]);
        if (j + 1 < pk.length) {
            var tm = (f + 0.5) * frameDur;
            tween.push([f + 0.5, pos.valueAtTime(tm, false)[0],
                                 pos.valueAtTime(tm, false)[1],
                                 rot.valueAtTime(tm, false)]);
        }
    }
    report.layers.push({
        id: L.id, name: L.name,
        pos_keys: pos.numKeys, rot_keys: rot.numKeys,
        stored: stored, tween: tween
    });
}
var tTotal = now() - tStart;

app.endUndoGroup();

// -------------------------------------------------------------------------
// Write the report
// -------------------------------------------------------------------------

var out = [];
out.push("{\"schema\":\"ae-physics-report/1\"");
out.push(",\"comp\":{\"name\":" + str(comp.name) + ",\"fps\":" + num(fps) +
         ",\"width\":" + comp.width + ",\"height\":" + comp.height + "}");
out.push(",\"total_ms\":" + tTotal);
out.push(",\"sample_stride\":" + SAMPLE_STRIDE);
out.push(",\"ae_version\":" + str(app.version));

var ts = [];
for (i = 0; i < report.timings.length; i++) {
    var T = report.timings[i];
    ts.push("{\"id\":" + T.id + ",\"name\":" + str(T.name) +
            ",\"keys\":" + T.keys + ",\"loop_ms\":" + T.loop_ms +
            ",\"bulk_ms\":" + T.bulk_ms + ",\"interp_ms\":" + T.interp_ms + "}");
}
out.push(",\"timings\":[" + ts.join(",") + "]");

var ls = [];
for (i = 0; i < report.layers.length; i++) {
    var R = report.layers[i];
    var st = [], tw = [];
    for (j = 0; j < R.stored.length; j++) {
        st.push("[" + R.stored[j][0] + "," + num(R.stored[j][1]) + "," +
                num(R.stored[j][2]) + "," + num(R.stored[j][3]) + "]");
    }
    for (j = 0; j < R.tween.length; j++) {
        tw.push("[" + num(R.tween[j][0]) + "," + num(R.tween[j][1]) + "," +
                num(R.tween[j][2]) + "," + num(R.tween[j][3]) + "]");
    }
    ls.push("{\"id\":" + R.id + ",\"name\":" + str(R.name) +
            ",\"pos_keys\":" + R.pos_keys + ",\"rot_keys\":" + R.rot_keys +
            ",\"stored\":[" + st.join(",") + "]" +
            ",\"tween\":[" + tw.join(",") + "]}");
}
out.push(",\"layers\":[" + ls.join(",") + "]}");

var rf = File.saveDialog("Save the apply report", "*.json");
if (rf !== null) {
    if (rf.name.indexOf(".json") === -1) rf = new File(rf.fsName + ".json");
    rf.encoding = "UTF-8";
    rf.open("w");
    rf.write(out.join(""));
    rf.close();
}

var totalKeys = 0;
for (i = 0; i < report.timings.length; i++) totalKeys += report.timings[i].keys;
alert("Applied " + totalKeys + " keyframes to " + bake.layers.length +
      " layer(s) in " + (tTotal / 1000).toFixed(2) + " s.\n\n" +
      (rf !== null ? "Report: " + rf.fsName : "No report saved."));

})();
