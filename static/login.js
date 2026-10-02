'use strict';
document.getElementById('form-login').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const error = document.getElementById('error');
  error.textContent = '';
  const btn = ev.target.querySelector('button');
  btn.disabled = true;
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({
        username: document.getElementById('usuario').value.trim(),
        password: document.getElementById('clave').value,
      }),
    });
    if (r.ok) { location.href = '/'; return; }
    const data = await r.json().catch(() => ({}));
    error.textContent = data.detail || 'No se pudo ingresar.';
  } catch (e) {
    error.textContent = 'No hay conexión con el servidor local. ¿Está corriendo run.py?';
  } finally {
    btn.disabled = false;
    document.getElementById('clave').value = '';
  }
});
