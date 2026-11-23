import numpy as np, json, math
from PIL import Image
from scipy import ndimage
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
im=np.array(Image.open('hi-1.png').convert('RGB')).astype(int)
R,G,B=im[...,0],im[...,1],im[...,2]
red=(R>200)&(G<110)&(B<110)
s=2482/993
pts=[((260,347),(548500,5390500)),((496.4,349),(549000,5390500)),((734,114),(549500,5391000)),((494.6,585),(549000,5390000)),((258,583.4),(548500,5390000)),((732.4,350),(549500,5390500))]
A=np.array([[x*s,y*s,1] for (x,y),_ in pts]);cE=np.linalg.lstsq(A,[e for _,(e,n) in pts],rcond=None)[0];cN=np.linalg.lstsq(A,[n for _,(e,n) in pts],rcond=None)[0]
mpp=math.hypot(cE[0],cN[0])
closed=ndimage.binary_closing(red,iterations=3)
filled=ndimage.binary_fill_holes(closed)
lab,n=ndimage.label(filled);k=np.argmax(ndimage.sum(filled,lab,range(1,n+1)))+1;reg=lab==k
# stroke width: distance transform inside red pixels belonging to region boundary
redreg=red&ndimage.binary_dilation(reg)
dt=ndimage.distance_transform_edt(redreg)
# stroke half-width ~ typical local max of dt along outer boundary
outer=reg&~ndimage.binary_erosion(reg)
# sample stroke width: for outer-boundary pixels, dt of nearest interior red pixel; use 90th pct of dt on red pixels
w_half=np.percentile(dt[redreg],95)
print('stroke half-width px',w_half,'=> m',w_half*mpp,'full stroke m',2*w_half*mpp,'m/px',mpp)
# centreline polygon: erode region by half stroke width via distance transform of region
dreg=ndimage.distance_transform_edt(reg)
cent=dreg>w_half
cs=plt.contour(dreg,levels=[w_half])
paths=cs.allsegs[0];ring=max(paths,key=len)
E=cE[0]*ring[:,0]+cE[1]*ring[:,1]+cE[2];N=cN[0]*ring[:,0]+cN[1]*ring[:,1]+cN[2]
utm=np.c_[E,N]
def area(p):x,y=p[:,0],p[:,1];return 0.5*abs(np.dot(x,np.roll(y,-1))-np.dot(y,np.roll(x,-1)))
outer_ring=max(plt.contour(reg.astype(float),levels=[0.5]).allsegs[0],key=len)
Eo=cE[0]*outer_ring[:,0]+cE[1]*outer_ring[:,1]+cE[2];No=cN[0]*outer_ring[:,0]+cN[1]*outer_ring[:,1]+cN[2]
print('area centreline ha',area(utm)/1e4,'outer edge ha',area(np.c_[Eo,No])/1e4,'n vertices',len(utm))
# simplify: Douglas-Peucker 0.5 m
def dp(p,eps):
    if len(p)<3:return p
    a,b=p[0],p[-1];ab=b-a;L=np.hypot(*ab) or 1
    q=p-a;d=np.abs(ab[0]*q[:,1]-ab[1]*q[:,0])/L;i=np.argmax(d)
    if d[i]>eps:return np.vstack([dp(p[:i+1],eps)[:-1],dp(p[i:],eps)])
    return np.vstack([a,b])
i2=np.argmax(np.hypot(*(utm-utm[0]).T));simp=np.vstack([dp(utm[:i2+1],0.5)[:-1],dp(np.vstack([utm[i2:],utm[:1]]),0.5)]);
if not np.allclose(simp[0],simp[-1]):simp=np.vstack([simp,simp[0]])
print('simplified vertices',len(simp),'area ha',area(simp)/1e4)
json.dump({"crs":"EPSG:25832","quelle":"Lageplan Eschenbach-Deponie 09.11.2026, Strichmitte, Douglas-Peucker 0.5 m","stroke_full_width_m":round(2*w_half*mpp,2),"georef_rms_m":0.4,"ring":[[round(x,2),round(y,2)] for x,y in simp]},open('g2_ring_25832.json','w'),indent=0)
