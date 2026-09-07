"""Audit-only Dukascopy BID snapshot, frozen exclusive UTC end."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import json, hashlib, os
import pandas as pd
END=pd.Timestamp('2026-08-12T00:00:00Z')
HOURS={'1h':1,'15m':.25,'5m':1/12}

def select_closed(frame,tf):
    frame=frame.copy()
    frame.index=pd.to_datetime(frame.index,utc=True)
    frame=frame[frame.index+pd.Timedelta(hours=HOURS[tf])<=END].tail(5000)
    assert len(frame)==5000, f'{tf}: insufficient candles {len(frame)}'
    assert frame.index.is_unique and frame.index.is_monotonic_increasing
    return frame

if __name__=='__main__':
    import dukascopy_python as d
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD
    import certifi,requests
    os.environ['REQUESTS_CA_BUNDLE']=certifi.where()
    out=Path(__file__).resolve().parent/'data';out.mkdir(exist_ok=True)
    # Preserve exact HTTPS response body plus normalized untrimmed fetch output.
    original=requests.get
    n=[0]
    def archived_get(*args,**kwargs):
        kwargs.update(verify=certifi.where(),timeout=60)
        r=original(*args,**kwargs);r.raise_for_status();n[0]+=1
        (out/f'response-{n[0]:03}.txt').write_bytes(r.content)
        (out/f'request-{n[0]:03}.json').write_text(json.dumps({'url':r.url,'status':r.status_code,'tls_verified':True},indent=2))
        return r
    requests.get=archived_get
    manifest={'source':'Dukascopy XAU/USD BID','exclusive_end':END.isoformat(),'tls_verified':True,'frames':{}}
    for tf,iv in [('1h',d.INTERVAL_HOUR_1),('15m',d.INTERVAL_MIN_15),('5m',d.INTERVAL_MIN_5)]:
        start=END.to_pydatetime()-timedelta(hours=HOURS[tf]*5000*2.2)
        raw=d.fetch(INSTRUMENT_FX_METALS_XAU_USD,iv,d.OFFER_SIDE_BID,start,END.to_pydatetime(),max_retries=3)
        assert raw is not None and not raw.empty
        raw.to_csv(out/f'raw_{tf}.csv')
        frame=select_closed(raw,tf);frame.to_csv(out/f'ohlc_{tf}.csv')
        manifest['frames'][tf]={'requested_start':start.isoformat(),'n':len(frame),'first':frame.index[0].isoformat(),'last':frame.index[-1].isoformat()}
        print(tf,manifest['frames'][tf],flush=True)
    manifest['hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
    (out/'fetch-manifest.json').write_text(json.dumps(manifest,indent=2))
