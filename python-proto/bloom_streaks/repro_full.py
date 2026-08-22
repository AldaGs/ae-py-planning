"""Reproduce the C++ FULL-res streak sweep exactly: CLAMPED bilinear fetch
(BL_Fetch replicates edges) + 2-buffer log-doubling with overshoot past L."""
import numpy as np

def fetch_clamped(A, fx, fy):
    """Bilinear with EDGE CLAMP, matching BL_Fetch."""
    h, w = A.shape
    fx = np.clip(fx, 0, w - 1); fy = np.clip(fy, 0, h - 1)
    x0 = np.floor(fx).astype(int); y0 = np.floor(fy).astype(int)
    x1 = np.minimum(x0 + 1, w - 1); y1 = np.minimum(y0 + 1, h - 1)
    tx = fx - x0; ty = fy - y0
    a0 = A[y0, x0] + (A[y0, x1] - A[y0, x0]) * tx
    a1 = A[y1, x0] + (A[y1, x1] - A[y1, x0]) * tx
    return a0 + (a1 - a0) * ty

def sweep_cpp(src, dx, dy, L, decay):
    h, w = src.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    A = src.copy()
    off = 1.0
    while off <= L:
        wk = decay ** off
        A = A + wk * fetch_clamped(A, xs - dx * off, ys - dy * off)
        off *= 2.0
    return A

# synthetic: bright HDR disc on black, like the user's white dot
H = W = 400
ys, xs = np.mgrid[0:H, 0:W]
src = np.zeros((H, W))
src[((xs - 200)**2 + (ys - 250)**2) <= 12**2] = 8.0

L = 125.0
eps = 0.02
decay = float(np.exp(np.log(eps) / L))
norm = (1.0 - decay) / decay
scale = 1.0 * norm

# vertical axis, direction (0,1) => smears upward (samples p - t*d)
dx, dy = np.cos(np.pi/2), np.sin(np.pi/2)
full = sweep_cpp(src, dx, dy, L, decay)
tail = scale * (full - src)

print(f"decay={decay:.5f} norm={norm:.4f} passes={int(np.floor(np.log2(L)))+1}")
for yy in (200, 180, 150, 120):
    prof = tail[yy, 185:216]
    print(f"\n y={yy} (dist {250-yy}px above disc) cross-section x=185..215:")
    print("  " + " ".join(f"{v:6.3f}" for v in prof))
    # count local maxima = number of distinct parallel lines
    pk = [i for i in range(1, len(prof)-1) if prof[i] > prof[i-1] and prof[i] > prof[i+1] and prof[i] > 1e-4]
    print(f"  local maxima: {len(pk)} at x={[185+i for i in pk]}")

print("\n=== where is the energy? column x=200 down the frame ===")
col = tail[:, 200]
nz = np.where(col > 1e-4)[0]
print(f"  nonzero rows {nz.min()}..{nz.max()} (disc at y=250)")
for yy in (270, 300, 330, 360):
    prof = tail[yy, 185:216]
    pk = [i for i in range(1,len(prof)-1) if prof[i]>prof[i-1] and prof[i]>prof[i+1] and prof[i]>1e-4]
    print(f"\n y={yy} (dist {yy-250} below disc): maxima={len(pk)} at {[185+i for i in pk]}")
    print("  " + " ".join(f"{v:6.3f}" for v in prof))
