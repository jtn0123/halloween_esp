; Castle Tools' one addition to Tauri's stock NSIS uninstaller
; (tauri.conf.json bundle > windows > nsis > installerHooks).
;
; The app sets its tools up on first launch in %LOCALAPPDATA%\<identifier>\
; runtime — Python, torch, ffmpeg, the htdemucs weights, about 1.7 GB
; (src/lib.rs, runtime_dir). The stock uninstaller removes it only with the
; owner's data, when "Delete the application data" is ticked. This removes
; the runtime on every owner's uninstall, ticked or not; the songs, the
; show and the settings (%APPDATA%\<identifier>) and the log stay unless the
; box is ticked, as before.
;
; The runtime holds a junction to those songs: Castle Radio's data folder,
; app\demo\castle-radio\.radio-data (tools/desktop_env.py RADIO_DATA,
; link_radio_data). RMDir /r follows a junction and empties its target — the
; first release smoke of this hook lost the owner's show that way. So the
; link goes first, on its own (RMDir without /r removes a junction and never
; its target), and while anything still answers through it the runtime is
; left where it is: 1.7 GB on the disk is the safe way to be wrong.
;
; Two uninstalls keep it, because the next start re-uses it:
;   * an update (/UPDATE, the app's own updater);
;   * a newer setup removing the old version first ("Uninstall before
;     installing"), which runs uninstall.exe in place (_?=). An owner's
;     uninstall (Settings, or uninstall.exe opened by hand) runs from a copy
;     the uninstaller makes in %TEMP%, so $EXEDIR is not $INSTDIR.
; tests/test_desktop_uninstall.py holds this file to that; release.yml's
; first-run smoke runs both uninstalls on Windows (tools/desktop_smoke.py).

!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
  ${AndIf} $EXEDIR != $INSTDIR
    SetShellVarContext current
    RMDir "$LOCALAPPDATA\${BUNDLEID}\runtime\app\demo\castle-radio\.radio-data"
    ${IfNot} ${FileExists} "$LOCALAPPDATA\${BUNDLEID}\runtime\app\demo\castle-radio\.radio-data\*.*"
      RMDir /r "$LOCALAPPDATA\${BUNDLEID}\runtime"
    ${EndIf}
  ${EndIf}
!macroend
