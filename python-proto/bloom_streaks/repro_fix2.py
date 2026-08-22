import numpy as np
exec(open('repro_full.py').read().split('# synthetic')[0])

H=W=400
ys,xs=np.mgrid[0:H,0:W]
src=np.zeros((H,W)); src[((xs-200)**2+(ys-250)**2)<=12**2]=8.0
L=125.0; decay=float(np.exp(np.log(0.02)/L)); scale=(1.0-decay)/decay
dx,dy=np.cos(np.pi/2),np.sin(np.pi/2)

def sepblur(a,k):
    k=np.asarray(k,float); k=k/k.sum()
    b=np.apply_along_axis(lambda m:np.convolve(m,k,'same'),1,a)
    return np.apply_along_axis(lambda m:np.convolve(m,k,'same'),0,b)

def interior_banding(p):
    """max step INSIDE the streak plateau (>60% of peak) - excludes the real
    outer edge, so this measures only the stripe artifact."""
    pk=p.max()
    if pk<=0: return 0.0,0.0
    m=p>0.6*pk
    q=p[m]
    if len(q)<3: return 0.0,pk
    return float(np.abs(np.diff(q)).max()), pk

# blur the SOURCE (sweep is linear+shift-invariant, so blur(sweep(s))==sweep(blur(s))
# -> pre-blurring the source once is equivalent and costs ONE pass, not one per axis)
variants={
 'raw (current Full)': src,
 'tent3 [1,2,1]'     : sepblur(src,[1,2,1]),
 'gauss5 [1,4,6,4,1]': sepblur(src,[1,4,6,4,1]),
}
print(f"{'variant':22} {'interior step':>14} {'peak':>8} {'step %':>8}")
for name,s in variants.items():
    t=scale*(sweep_cpp(s,dx,dy,L,decay)-s)
    for yy in (270,):
        st,pk=interior_banding(t[yy,180:221])
        print(f"{name:22} {st:14.4f} {pk:8.3f} {100*st/max(pk,1e-9):7.2f}%")

print("\ncross-sections at y=270 (x=192..209):")
for name,s in variants.items():
    t=scale*(sweep_cpp(s,dx,dy,L,decay)-s)
    print(f"  {name:20}", " ".join(f"{v:5.2f}" for v in t[270,192:210]))

# tip sharpness along the axis, to confirm the blur costs ~nothing there
print("\ntip along axis (x=200, y=358..368):")
for name,s in variants.items():
    t=scale*(sweep_cpp(s,dx,dy,L,decay)-s)
    print(f"  {name:20}", " ".join(f"{v:5.3f}" for v in t[358:369,200]))
