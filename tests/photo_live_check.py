"""Opt-in GPU integration check: python3 tests/photo_live_check.py.

Launches only vkcube. Exercises asynchronous presentation under stalled inference,
effect toggles, and helper stop/resume without closing the game.
"""
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'launcher'))
import backend as b
import photo_runtime

p={**b.DEFAULTS,'renderer':'photo','mode':'direct','width':1280,'height':720,'photo_fps':0}
g=b.Game('test:photo-live','Photo transport test','/tmp',[
    b.LaunchOption('/usr/bin/vkcube','--c 9000 --width 1280 --height 720','/tmp')])
proc=None
paused=None
report={}
try:
    b.start_helper(p); b.apply_preferences(p)
    plan=b.launch_plan(g,p)
    plan.environment['XDG_STATE_HOME']=str(b.ROOT/'verification/photo-capture')
    proc=b.spawn_game(plan)
    monitor=b.Telemetry()
    samples=[]
    for i in range(7):
        time.sleep(1)
        s=monitor.read()
        samples.append({k:s.get(k) for k in ('state','rate','successful','rendered','helperEvalMsBits','layerMsBits')})
    report['samples']=samples
    assert all(s['rate']>=30 for s in samples[2:]), samples
    b.control('capture','1')
    before=monitor.read()['successful']
    paused=photo_runtime.status()['pid']
    os.kill(paused,signal.SIGSTOP)
    time.sleep(1.3)
    stalled=monitor.read()['successful']
    assert stalled<=before+1
    entries=re.findall(r'passed through=(\d+)',Path(plan.log).read_text())
    assert entries and int(entries[-1])>60, 'Presentation did not advance during stalled inference'
    report['stalled_helper_passthrough_frames']=int(entries[-1])
    os.kill(paused,signal.SIGCONT); paused=None
    time.sleep(1)
    assert monitor.read()['successful']>stalled+10
    b.control('set','enabled','0'); time.sleep(.15)
    before=monitor.read()['successful']; time.sleep(.6)
    assert monitor.read()['successful']==before
    b.control('set','enabled','1'); time.sleep(1)
    assert monitor.read()['successful']>before+10
    report['effect_toggle']='passed'
    b.stop_helper()
    assert proc.poll() is None
    report['stop_keeps_game_open']='passed'
    before=monitor.read()['successful']
    b.start_helper(p); b.apply_preferences(p); time.sleep(2)
    assert monitor.read()['successful']>before+10
    report['resume']='passed'
    report['log']=plan.log
    print(json.dumps(report,indent=2))
    (b.ROOT/'verification/photo-live-check.json').write_text(json.dumps(report,indent=2))
finally:
    if paused:
        os.kill(paused,signal.SIGCONT)
    if proc and proc.poll() is None:
        proc.terminate(); proc.wait(timeout=10)
    b.stop_helper()
