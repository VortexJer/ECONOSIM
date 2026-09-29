# Gates: entrenamiento en el sobremesa (training/SOBREMESA.md)

OWNS: data/market/**, data/news/**, training/data/**, training/adapters/**, training/RESULTADOS.md, .unlazy/sobremesa/**

Scope: seguir training/SOBREMESA.md de principio a fin en este PC: entorno, datos, demostraciones, prueba de velocidad, entrenamiento demos-v1, sección 9 en RESULTADOS.md, commit y push.

- [x] G0: this ledger states outcomes that can fail
  CHECK: node C:/Users/escri/.claude/skills/unlazy/scripts/gate-lint.mjs .unlazy/sobremesa/GATES.md
  EXPECT: LINT OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=654b07bdcc3a231967e95cc9a096d1bf795ba70b1795e8eb5b887a70f06b1405; exit=0; EXPECT=matched; output-sha256=315c59b14adf22b0a75bea0bcf6880c407656ea58fdf48ddbdd07ef547d8f592; output-bytes=351; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\escri\OneDrive\Escritorio\econosim; path=4244101bee36/36 entries

- [x] G1: el entorno de training/.venv usa Python 3.13 con las versiones pedidas y ve la GPU
  CHECK: training\.venv\Scripts\python.exe .unlazy/sobremesa/verify.py entorno
  EXPECT: VERIFICADO entorno
  EVIDENCE: automatic-evidence=v1; definition-sha256=8d56a11b4395a3439b8bd7fb2b932c7dac671c806e17d264ea0a4942d41a3768; exit=0; EXPECT=matched; output-sha256=79e3d3d9a2710c627e43c1dfbdce2f92736e459142542505c0d73828409ba2e6; output-bytes=49; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\escri\OneDrive\Escritorio\econosim; path=4244101bee36/36 entries

- [x] G2: data/market tiene todos los símbolos del universo con precios hasta hace menos de 10 días
  CHECK: training\.venv\Scripts\python.exe .unlazy/sobremesa/verify.py mercado
  EXPECT: VERIFICADO mercado
  EVIDENCE: automatic-evidence=v1; definition-sha256=0cc20171f90c2a67db64ef6caad7b13eb7604bc263905addfda8c499125a6474; exit=0; EXPECT=matched; output-sha256=51bbf9588ed5daa3703bac5a33e7ad5bc90e3e4ed91b776a9f5aca278b00ca87; output-bytes=44; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\escri\OneDrive\Escritorio\econosim; path=4244101bee36/36 entries

- [x] G3: cada titular de headlines.parquet tiene su tono FinBERT en scored.parquet
  CHECK: training\.venv\Scripts\python.exe .unlazy/sobremesa/verify.py noticias
  EXPECT: VERIFICADO noticias
  EVIDENCE: automatic-evidence=v1; definition-sha256=2c2fc854939f136598770ad6859c06828048ccf3bdfb26ad294aa90f6ba27b6f; exit=0; EXPECT=matched; output-sha256=875516163ea24428babf106cc77a9f3aa12d07d045ea83a3a997ef39fbd35a39; output-bytes=132; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\escri\OneDrive\Escritorio\econosim; path=4244101bee36/36 entries

- [x] G4: demos.jsonl tiene las sesiones de 1000 vidas, al menos el 95 % sin errores
  CHECK: training\.venv\Scripts\python.exe .unlazy/sobremesa/verify.py demos
  EXPECT: VERIFICADO demos
  EVIDENCE: automatic-evidence=v1; definition-sha256=075058db92813a5eede230773265be3c2d5bbb82829650d8cf10be8925a4673f; exit=0; EXPECT=matched; output-sha256=f52fd3713172021dd6f4db2179d33511a6b21ab77225ef93b30446862c8b0454; output-bytes=58; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\escri\OneDrive\Escritorio\econosim; path=4244101bee36/36 entries

- [x] G5: la prueba de velocidad terminó y su log tiene los s/it
  CHECK: training\.venv\Scripts\python.exe .unlazy/sobremesa/verify.py velocidad
  EXPECT: VERIFICADO velocidad
  EVIDENCE: automatic-evidence=v1; definition-sha256=374a050e4cdcc5d18882184fb25f1849832f39f61008172e1202eb425b0f8aad; exit=0; EXPECT=matched; output-sha256=38e4ed2b79f09f7390a28c86fedf43c7d6ce99548f9f8826d9ac19591dd6963d; output-bytes=65; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\escri\OneDrive\Escritorio\econosim; path=4244101bee36/36 entries

- [x] G6: el adaptador demos-v1 existe y el log acaba con una pérdida de validación válida
  CHECK: training\.venv\Scripts\python.exe .unlazy/sobremesa/verify.py entrenamiento
  EXPECT: VERIFICADO entrenamiento
  EVIDENCE: automatic-evidence=v1; definition-sha256=61153ecd1f84175918e6fb4abdcd2f4c901ed125831a404f4b4963aa12260abd; exit=0; EXPECT=matched; output-sha256=503841a85ba0718a41b49ae48dc77683fb233559d9e6fa669758a2e79198357f; output-bytes=70; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\escri\OneDrive\Escritorio\econosim; path=4244101bee36/36 entries

- [ ] G7: RESULTADOS.md tiene la sección 9 con la GPU y la pérdida de validación del log
  CHECK: training\.venv\Scripts\python.exe .unlazy/sobremesa/verify.py resultados
  EXPECT: VERIFICADO resultados
  EVIDENCE: pending

- [ ] G8: el commit con la sección 9 está en GitHub y ningún adaptador está en git
  CHECK: training\.venv\Scripts\python.exe .unlazy/sobremesa/verify.py publicado
  EXPECT: VERIFICADO publicado
  EVIDENCE: pending

- [ ] G9: la sección 9 cuenta con fidelidad GPU, sesiones, s/sesión y pérdidas de entrenamiento y validación
  EVIDENCE: pending
