; Refuse to overwrite running Locus binaries. NSIS's default per-file Retry / Ignore
; prompt can otherwise leave a mixed-version install when an active sidecar is locked.
!include "LogicLib.nsh"
!include "StrFunc.nsh"
${Using:StrFunc} StrLoc

!macro LOCUS_REQUIRE_PROCESS_CLOSED IMAGE_NAME
  nsExec::ExecToStack /TIMEOUT=5000 '"$SYSDIR\tasklist.exe" /FI "IMAGENAME eq ${IMAGE_NAME}" /FO CSV /NH'
  Pop $R0 ; exit code, or "error" / "timeout"
  Pop $R1 ; tasklist output
  ${If} $R0 != "0"
    MessageBox MB_ICONSTOP|MB_OK "The installer could not confirm that Lattix Locus is closed. Save your work, quit Locus from the system tray, then run the installer again. No files have been changed."
    Quit
  ${EndIf}
  ${StrLoc} $R2 $R1 "${IMAGE_NAME}" ">"
  ${If} $R2 != ""
    MessageBox MB_ICONSTOP|MB_OK "Lattix Locus is still running. Save your work, close every Locus window, and choose Quit from the system tray. Then run the installer again. No files have been changed."
    Quit
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREINSTALL
  Push $R0
  Push $R1
  Push $R2
  !insertmacro LOCUS_REQUIRE_PROCESS_CLOSED "lattix-locus-desktop.exe"
  !insertmacro LOCUS_REQUIRE_PROCESS_CLOSED "locus-backend.exe"
  !insertmacro LOCUS_REQUIRE_PROCESS_CLOSED "locus-opa.exe"
  Pop $R2
  Pop $R1
  Pop $R0
!macroend
