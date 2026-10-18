"""Frozen pattern rules (double inside bar). DO NOT EDIT: paper/ results are only valid for this exact file.
Fixed parameters: big_k=1.8, small_frac=0.5, wait=3, tp_r=2.0, fee=0.0006, body_exit=True, no regime filter,
max_hold=30 bars (4h) / 20 bars (1d). signals() is copied verbatim from the research backtest."""
import numpy as np, pandas as pd

BIG_K, SMALL_FRAC, WAIT, TP_R, FEE, BODY_EXIT = 1.8, 0.5, 3, 2.0, 0.0006, True
MAX_HOLD = {"4h": 30, "1d": 20}
MAX_RISK_FRAC = 0.25

def add_atr(df):
    tr=pd.concat([df.high-df.low,(df.high-df.close.shift()).abs(),(df.low-df.close.shift()).abs()],axis=1).max(axis=1)
    df["atr"]=tr.rolling(14).mean()
    return df

def signals(df,big_k=1.8,small_frac=0.5,wait=3):
    o,h,l,c,a=[df[x].values for x in ("open","high","low","close","atr")]
    body=np.abs(c-o); n=len(df); sigs=[]
    for i in range(15,n-2):
        if np.isnan(a[i-1]): continue
        # big move: single bar body >= big_k*ATR, or 2 consecutive same-direction bars whose combined move >= big_k*1.3*ATR
        d=np.sign(c[i]-o[i]); big=body[i]>=big_k*a[i-1]
        hi,lo=h[i],l[i]
        if not big and np.sign(c[i-1]-o[i-1])==d and abs(c[i]-o[i-1])>=big_k*1.3*a[i-1]:
            big=True; hi=max(h[i],h[i-1]); lo=min(l[i],l[i-1])
        if not big or d==0: continue
        j1,j2=i+1,i+2
        if j2>=n: break
        ok=all(lo<=min(o[j],c[j]) and max(o[j],c[j])<=hi and body[j]<=small_frac*abs(c[i]-o[i]) for j in (j1,j2))
        if not ok: continue
        top=max(o[j1],c[j1],o[j2],c[j2]); bot=min(o[j1],c[j1],o[j2],c[j2])
        zone_hi=max(h[j1],h[j2]); zone_lo=min(l[j1],l[j2])
        for k in range(j2+1,min(n,j2+1+wait)):
            if c[k]>top: sigs.append(dict(i=k,side=1,top=top,bot=bot,stop=min(zone_lo,l[k]),move=int(d))); break
            if c[k]<bot: sigs.append(dict(i=k,side=-1,top=top,bot=bot,stop=max(zone_hi,h[k]),move=int(d))); break
    return sigs

def simulate(df,sigs,tp_r=2.0,max_hold=20,fee=0.0006,body_exit=True):
    """Reference batch simulator (verbatim logic of the research backtest, regime=None). Used only for the self-check."""
    h,l,c=df.high.values,df.low.values,df.close.values; out=[];busy=-1
    for s in sigs:
        i=s["i"]
        if i<=busy: continue
        side=s["side"]; e=c[i]; stp=s["stop"]; risk=abs(e-stp)
        if risk<=0 or risk/e>0.25: continue
        tp=e+side*tp_r*risk; ex=None
        for k in range(i+1,min(len(c),i+1+max_hold)):
            if (side==1 and l[k]<=stp) or (side==-1 and h[k]>=stp): ex=(k,stp,"stop");break
            if (side==1 and h[k]>=tp) or (side==-1 and l[k]<=tp): ex=(k,tp,"tp");break
            if body_exit and ((side==1 and c[k]<s["bot"]) or (side==-1 and c[k]>s["top"])): ex=(k,c[k],"body");break
        complete=ex is not None or i+max_hold<=len(c)-1
        if ex is None:
            k=min(len(c)-1,i+max_hold); ex=(k,c[k],"time")
        r=side*(ex[1]-e)/risk - fee*e/risk
        out.append(dict(i=i,side=side,entry=e,stop=stp,exit_i=ex[0],exit=ex[1],why=ex[2],R=r,complete=complete))
        busy=ex[0]
    return out
