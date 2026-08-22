import numpy as np
exec(open('repro_full.py').read().split('# synthetic')[0])

H=W=400
ys,xs=np.mgrid[0:H,0:W]
src=np.zeros((H,W)); src[((xs-200)**2+(ys-250)**2)<=12**2]=8.0
L=125.0; decay=float(np.exp(np.log(0.02)/L)); scale=(1.0-decay)/decay

# the disc's own column heights - the source of the staircase
chords=(src>0).sum(axis=0)[188:213]
print("disc column heights (px):", list(chords))
print("=> the vertical sweep integrates each column INDEPENDENTLY, so these")
print("   integer chord lengths become brightness steps = parallel stripes.\n")

dx,dy=np.cos(np.pi/2),np.sin(np.pi/2)
tail=scale*(sweep_cpp(src,dx,dy,L,decay)-src)

def tent3(a):
    k=np.array([1,2,1],float); k/=k.sum()
    b=np.apply_along_axis(lambda m:np.convolve(m,k,'same'),1,a)
    return np.apply_along_axis(lambda m:np.convolve(m,k,'same'),0,b)

def banding(p):
    d=np.abs(np.diff(p[p>1e-6]))
    return d.max() if len(d) else 0.0

blur=tent3(tail)
print(f"{'y':>5} {'raw max step':>13} {'tent3 max step':>15}  reduction")
for yy in (270,300,330,360):
    r=banding(tail[yy,185:216]); b=banding(blur[yy,185:216])
    print(f"{yy:5d} {r:13.4f} {b:15.4f}  {r/max(b,1e-9):5.1f}x")

# does the tent hurt the TIP (sharpness along the axis)?
print("\ntip profile down the axis (x=200), raw vs tent3:")
print("  raw :", " ".join(f"{v:5.2f}" for v in tail[355:370,200]))
print("  tent:", " ".join(f"{v:5.2f}" for v in blur[355:370,200]))
