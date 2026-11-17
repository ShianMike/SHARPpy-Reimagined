; Retain Tauri's stock installer and its updater/shortcut behavior.
!define SHARPPY_CLEANUP_EXE "${__FILEDIR__}\..\target\release\installer-cleanup.exe"

!macro SHARPPY_RUNTIME_STEP MODE
  ClearErrors
  ExecWait '"$PLUGINSDIR\sharppy-installer-cleanup.exe" ${MODE} "$INSTDIR" "$PLUGINSDIR\previous-runtime-files"' $0
  ${If} ${Errors}
  ${OrIf} $0 != 0
    MessageBox MB_ICONSTOP|MB_OK "SHARPpy could not safely update its runtime files. Close running SHARPpy processes and verify the selected installation folder, then run this installer again. Your soundings and preferences are preserved."
    SetErrorLevel 2
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
!macroend

!macro NSIS_HOOK_PREINSTALL
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File /oname=sharppy-installer-cleanup.exe "${SHARPPY_CLEANUP_EXE}"
  ; Check paths and retain the old file list before resources are overwritten.
  !insertmacro SHARPPY_RUNTIME_STEP "--prepare"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  !insertmacro SHARPPY_RUNTIME_STEP "--prune"
!macroend
