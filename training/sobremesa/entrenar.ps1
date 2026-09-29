# Entrenamiento demos-v1 sin que Windows se suspenda (no cambia la configuración de energía).
# El log se escribe con cmd (y no con *> de PowerShell 5.1, que lo retenía hasta el final) para verlo en vivo.
Add-Type -Namespace W -Name P -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
# en decimal: PowerShell lee 0x80000001 como Int32 negativo y la llamada fallaba en silencio (nunca impidió dormir)
if ([W.P]::SetThreadExecutionState([uint32]2147483649) -eq 0) { throw "Windows no aceptó el aviso de no dormir" }   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
Set-Location C:\Users\escri\OneDrive\Escritorio\econosim\training
Remove-Item data\train_v1.fin -ErrorAction SilentlyContinue       # la marca de fin de una ejecución anterior confundía al vigilante
Set-Content -Path data\train_v1.inicio -Value (Get-Date -Format o)
try {
    cmd /c ".venv\Scripts\python.exe -u train_qlora.py --data data/demos.jsonl --out adapters/demos-v1 --val 0.03 --epochs 1 --max-rows 2200 > data\train_v1.log 2>&1"
    Set-Content -Path data\train_v1.fin -Value "$(Get-Date -Format o) salida=$LASTEXITCODE"
} finally {
    [W.P]::SetThreadExecutionState([uint32]2147483648) | Out-Null   # vuelve a lo normal (ES_CONTINUOUS)
}
