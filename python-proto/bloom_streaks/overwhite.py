import numpy as np
def enc(x): 
    x=np.clip(x,0,None); return np.where(x<=0.0031308,x*12.92,1.055*x**(1/2.4)-0.055)

# a streak pixel: swept LINEAR light g_rgb (premult) and swept COVERAGE g_a.
# Replicate the composite tail for a transparent-bg pixel (art alpha a=0).
def composite(g_rgb, g_a, scatter, gain, alpha_mode):
    k = scatter*gain
    glowA = np.clip(g_a*scatter,0,1)
    if glowA>1e-4:
        st = (g_rgb*k)/glowA          # straighten
        disp = enc(st*glowA)          # premult-in-linear
    else:
        disp = np.zeros(3)
    outPx = np.clip(disp,0,1)          # art=0 on transparent
    mx = outPx.max()
    if alpha_mode=='coverage':         # CURRENT: outA=glowA (a=0), raise to mx
        outA = glowA
        finalA = min(max(outA,mx),1)
    else:                              # PROPOSED: alpha tracks brightness only
        finalA = min(mx,1)
    # AE composites premult OVER a background:
    over_black = outPx + (1-finalA)*0.0
    over_white = outPx + (1-finalA)*1.0
    return outPx, finalA, over_black, over_white

print("scatter=0.5 gain=1  (a streak of NEUTRAL light, varying brightness)")
print(f"{'g_rgb':>6} {'g_a':>5} | {'mode':9} {'finalA':>7} {'overBLACK':>20} {'overWHITE':>20}")
for gr,ga in [(2.0,1.0),(0.6,1.0),(0.2,1.0),(0.6,3.0),(0.2,5.0)]:
    for mode in ('coverage','brightness'):
        g=np.array([gr,gr,gr])
        px,fa,ob,ow=composite(g,ga,0.5,1.0,mode)
        print(f"{gr:6.2f} {ga:5.1f} | {mode:9} {fa:7.3f}  ({ob[0]:.2f},{ob[1]:.2f},{ob[2]:.2f})      ({ow[0]:.2f},{ow[1]:.2f},{ow[2]:.2f})")
    print()
