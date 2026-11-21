#!/usr/bin/env python3
"""Register the lg-input KDE global shortcuts with KWin's kglobalaccel (persists to kglobalshortcutsrc)."""
import dbus
from PyQt6.QtGui import QKeySequence
SHORTCUTS = {'net.local.lg-input-toggle.desktop': ('LG monitor: toggle host / VM', 'Meta+Ctrl+F11'),
             'net.local.lg-input-rescue.desktop': ('LG monitor: rescue host displays', 'Meta+Ctrl+Shift+F11')}
ga = dbus.Interface(dbus.SessionBus().get_object('org.kde.kglobalaccel', '/kglobalaccel'), 'org.kde.KGlobalAccel')
def seq(s):
    ks = QKeySequence(s)
    return dbus.Struct((dbus.Array([dbus.Int32(ks[i].toCombined()) for i in range(ks.count())], signature='i'),), signature='ai')
for comp, (name, keys) in SHORTCUTS.items():
    owners = ga.globalShortcutsByKey(seq(keys), dbus.Struct((dbus.Int32(0),), signature='i'))
    others = [f'{o[2]}/{o[0]}' for o in owners if str(o[2]) != comp]
    if others:
        print(f'SKIP {keys}: already used by {others}'); continue
    aid = dbus.Array([comp, '_launch', name, 'Launch'], signature='s')
    ga.doRegister(aid)
    res = ga.setShortcutKeys(aid, dbus.Array([seq(keys)], signature='(ai)'), dbus.UInt32(4))  # 4 = NoAutoloading: force these keys
    print(comp, keys, '->', 'ok' if res and len(res[0][0]) else 'NOT SET')
