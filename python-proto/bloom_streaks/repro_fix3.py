import numpy as np
exec(open('repro_full.py').read().split('# synthetic')[0])
H=W=400; ys,xs=np.mgrid[0:H,0:W]
src=np.zeros((H,W)); src[((xs-200)**2+(ys-250)**2)<=12**2]=8.0
L=125.0; decay=float(np.exp(np.log(0.02)/L)); scale=(1.0-decay)/decay
dx,dy=np.cos(np.pi/2),np.sin(np.pi/2)
def sepblur(a,k):
    k=np.asarray(k,float); k/=k.sum()
    b=np.apply_along_axis(lambda m:np.convolve(m,k,'same'),1,a)
    return np.apply_along_axis(lambda m:np.convolve(m,k,'same'),0,b)
def kink(p):
    """max |2nd difference| = staircase roughness. A smooth bell -> ~0;
    flat runs punctuated by jumps -> large."""
    q=p[p>0.5*p.max()]
    return float(np.abs(np.diff(np.diff(q))).max()) if len(q)>3 else 0.0

print(f"{'variant':22} {'kink(y=270)':>12} {'kink(y=330)':>12}   stripes?")
for name,k in (('raw (current Full)',None),('tent3',[1,2,1]),('gauss5',[1,4,6,4,1])):
    s=src if k is None else sepblur(src,k)
    t=scale*(sweep_cpp(s,dx,dy,L,decay)-s)
    k1,k2=kink(t[270,180:221]),kink(t[330,180:221])
    print(f"{name:22} {k1:12.4f} {k2:12.4f}   {'YES' if k1>0.15 else 'no'}")

# the giveaway: do bright columns persist the WHOLE length? (per-column independence)
s=src; t=scale*(sweep_cpp(s,dx,dy,L,decay)-s)
c=t[300,180:221]; peak=c.max()
print(f"\nraw: columns at y=300 within 1% of a neighbour-exceeding level:")
print("   ", " ".join(f"{v/peak:4.2f}" for v in c[12:29]))
sg=sepblur(src,[1,4,6,4,1]); tg=scale*(sweep_cpp(sg,dx,dy,L,decay)-sg)
cg=tg[300,180:221]; pg=cg.max()
print(f"gauss5 same row:")
print("   ", " ".join(f"{v/pg:4.2f}" for v in cg[12:29]))
