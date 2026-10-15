'use strict';
/* Backlog ASP. Todo el texto de los datos se inserta con textContent (nunca innerHTML). */

const S = {
  me: null, casos: [], hoy: null, kgbFecha: null, cargado: {},
  f: { q: '', opcion: '', tienda: '', estado: '', alerta: '', guia: '', superAsp: false, cerrados: false },
  grupo: '', orden: 'prioridad', colapsados: new Set(), sel: null,
};
const puedeEditar = () => !!(S.me && S.me.puede_editar);

// ------------------------------------------------------------------ utilidades
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else if (k === 'checked') el.checked = !!v;
    else if (k === 'selected') el.selected = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const $ = (s) => document.querySelector(s);
const vaciar = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

function aDate(iso) { if (!iso) return null; const [y, m, d] = iso.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); }
const DIAS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];
const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
const fLarga = new Intl.DateTimeFormat('es-CO', { day: '2-digit', month: '2-digit', year: 'numeric' });
const fecha = (iso) => { if (!iso) return '—'; const d = aDate(iso); return `${DIAS[d.getDay()]} ${d.getDate()} ${MESES[d.getMonth()]}`; };
const fechaLarga = (iso) => (iso ? fLarga.format(aDate(iso)) : '—');
const diasEntre = (a, b) => Math.round((aDate(b) - aDate(a)) / 86400000);
const dinero = (v, mon) => (v === null || v === undefined ? '—'
  : new Intl.NumberFormat('es-CO', { style: 'currency', currency: mon || 'USD', maximumFractionDigits: 0 }).format(v));
const hoyISO = () => S.hoy || new Date().toISOString().slice(0, 10);

let avisoT;
function aviso(msg, error) {
  const el = $('#aviso');
  el.textContent = msg; el.className = error ? 'error' : ''; el.hidden = false;
  clearTimeout(avisoT); avisoT = setTimeout(() => { el.hidden = true; }, error ? 6000 : 3500);
}

async function api(method, url, body, raw) {
  const init = { method, headers: {}, credentials: 'same-origin' };
  if (method !== 'GET') init.headers['X-CSRF-Token'] = S.me ? S.me.csrf : '';
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch(url, init);
  if (r.status === 401) { location.href = '/login'; throw new Error('Sesión expirada.'); }
  if (raw && r.ok) return r;
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) {
    let msg = data && data.detail ? data.detail : r.statusText;
    if (Array.isArray(msg)) msg = msg.map((x) => x.msg).join('; ');
    throw new Error(msg);
  }
  return data;
}
const intentar = (fn) => async (...a) => { try { await fn(...a); } catch (e) { aviso(e.message, true); } };

function descargar(blob, nombre) {
  const a = h('a', { href: URL.createObjectURL(blob), download: nombre });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

// ------------------------------------------------------------------ arranque
async function iniciar() {
  S.me = await api('GET', '/api/me');
  S.hoy = S.me.hoy;
  $('#quien').textContent = `${S.me.full_name || S.me.username} (${S.me.rol_nombre || S.me.role})`;
  $('#tab-admin').hidden = S.me.role !== 'admin';
  if (S.me.pestanas) document.querySelectorAll('#tabs button').forEach((b) => { b.hidden = !S.me.pestanas.includes(b.dataset.tab); });
  document.body.classList.toggle('sin-precios', !S.me.ver_precios);
  $('#salir').addEventListener('click', intentar(async () => { await api('POST', '/api/logout'); location.href = '/login'; }));
  document.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => mostrar(b.dataset.tab)));
  if (S.me.must_change_pw) return cambiarClave(true);
  if (S.me.role === 'tienda') { $('#fuentes').hidden = true; mostrar('tienda'); return; }
  pintarFuentes();
  await cargarCasos();
  mostrar('casos');
}

function pintarFuentes() {
  const u = S.me.ultimas_cargas || {};
  const item = (label, iso) => {
    const viejo = iso && diasEntre(iso, hoyISO()) > 1;
    return h('span', {}, `${label}: `, h('strong', { class: viejo ? 'viejo' : '' }, iso ? fecha(iso) : 'sin cargar'));
  };
  vaciar($('#fuentes')).append(
    h('span', {}, 'Hoy: ', h('strong', {}, fecha(hoyISO()))),
    item('Backlog', u.backlog), item('KGB', u.kgb), item('Open Orders', u.openorders), item('Inventario', u.inventario), item('Precios', u.precios), item('Parts Data', u.partes), item('Tiendas', u.shipto),
    h('span', {}, `Fill rate: despacho hasta ETA + ${S.me.tolerancia} días`),
  );
}

const vistas = { casos: pintarCasos, guias: pintarGuias, partes: pintarPartes, correos: pintarCorreos, indicadores: pintarIndicadores, cargas: pintarCargas, admin: pintarAdmin, tienda: () => pintarTienda() };
function mostrar(tab) {
  document.querySelectorAll('#tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  document.querySelectorAll('.vista').forEach((v) => { v.hidden = v.id !== `v-${tab}`; });
  intentar(vistas[tab])();
}

async function cargarCasos() {
  const r = await api('GET', `/api/cases?incluir_cerrados=${S.f.cerrados ? 1 : 0}`);
  S.casos = r.casos; S.hoy = r.hoy; S.kgbFecha = r.kgb_fecha;
}

// ------------------------------------------------------------------ casos
function opciones(valores, actual, todos) {
  return [h('option', { value: '' }, todos), ...valores.map((v) => h('option', { value: v, selected: v === actual }, v))];
}
const unicos = (arr) => [...new Set(arr.filter(Boolean))].sort((a, b) => a.localeCompare(b, 'es'));

function filtrados() {
  const f = S.f; const q = f.q.trim().toLowerCase();
  return S.casos.filter((c) => {
    if (f.opcion && c.option_short !== f.opcion) return false;
    if (f.tienda && c.list_name !== f.tienda) return false;
    if (f.estado && c.estado !== f.estado) return false;
    if (f.superAsp && !c.super_asp) return false;
    if (f.guia && !c.tracking_list.includes(f.guia)) return false;
    if (f.alerta === 'rojo' && c.nivel !== 'rojo') return false;
    if (f.alerta === 'con' && !['rojo', 'ambar'].includes(c.nivel)) return false;
    if (f.alerta === 'sin' && ['rojo', 'ambar'].includes(c.nivel)) return false;
    if (f.alerta.startsWith('code:') && !c.alerts.some((a) => a.code === f.alerta.slice(5))) return false;
    if (q) {
      const txt = [c.key_notif, c.list_name, c.assembly, c.description, c.po_number, c.serial_no, c.comments_jenny,
        c.responsable, ...c.tracking_list].join(' ').toLowerCase();
      if (!txt.includes(q)) return false;
    }
    return true;
  });
}

const ORDENES = {
  prioridad: (a, b) => a.prioridad - b.prioridad,
  limite: (a, b) => (a.dias_limite ?? 9999) - (b.dias_limite ?? 9999),
  eta: (a, b) => (a.dias_eta ?? 9999) - (b.dias_eta ?? 9999),
  edad: (a, b) => (b.edad ?? 0) - (a.edad ?? 0),
  edadDespacho: (a, b) => (b.edad_despacho ?? 0) - (a.edad_despacho ?? 0),
  tienda: (a, b) => (a.list_name || '').localeCompare(b.list_name || '', 'es'),
};
const GRUPOS = {
  tienda: (c) => c.list_name || c.ship_to,
  opcion: (c) => c.option_short,
  estado: (c) => (c.source_status === 'cerrado' ? 'Cerrado en el reporte' : c.estado),
  guia: (c) => c.tracking_list[0] || 'Sin guía',
  parte: (c) => `${c.assembly} ${c.description || ''}`.trim(),
  nivel: (c) => ({ rojo: 'Alerta roja', ambar: 'Alerta ámbar', info: 'Informativo', ok: 'Sin alertas' }[c.nivel]),
};

const ALERTAS_NOMBRE = {
  FUERA_FR: 'Venció el plazo de fill rate', RIESGO_FR: 'Despacho después del límite', SIN_MARGEN: 'Sin margen en el plazo',
  OH_DEMORADO: 'OH demorado', ETA_NO_HABIL: 'ETA en día no hábil', GUIA_NO_KGB: 'Guía no aparece en KGB',
  GUIA_SIN_PARTE: 'Guía sin la parte', ETA_ANTES_KGB: 'ETA antes de la llegada KGB', GUIA_RETRASADA: 'Guía retrasada', FUERA_SLA_APPLE: 'Fuera de SLA Apple',
  SIN_RECEPCION: 'Llegada sin recepción', POSIBLE_DOBLE_COMPRA: 'Miami con parte en KGB', KAM_PENDIENTE: 'Esperando a la ACM',
  DESPACHADO_SIGUE: 'Despachado y sigue en reporte', REABIERTO: 'Reabierto', SIN_GUIA: 'In Transit sin guía',
  INV_DISPONIBLE: 'Se puede despachar con inventario propio', INV_OH_SIN_STOCK: 'OH sin inventario',
  MIAMI_EVALUAR: 'Miami: Apple lo trae antes', MIAMI_ATRASADA: 'Miami atrasada', MIAMI_SIN_COMPRA: 'Miami sin compra registrada',
};

function pintarCasos() {
  const v = vaciar($('#v-casos'));
  const f = S.f;
  const set = (k) => (ev) => { f[k] = ev.target.type === 'checkbox' ? ev.target.checked : ev.target.value; pintarTabla(); };
  const codigos = unicos(S.casos.flatMap((c) => c.alerts.map((a) => a.code)));
  v.append(h('div', { class: 'barra' },
    h('label', { class: 'campo' }, 'Buscar',
      h('input', { type: 'search', value: f.q, placeholder: 'Caso, parte, PO, guía, serial', oninput: set('q') })),
    h('label', { class: 'campo' }, 'Solución', h('select', { onchange: set('opcion') }, opciones(unicos(S.casos.map((c) => c.option_short)), f.opcion, 'Todas'))),
    h('label', { class: 'campo' }, 'Tienda', h('select', { onchange: set('tienda') }, opciones(unicos(S.casos.map((c) => c.list_name)), f.tienda, 'Todas'))),
    h('label', { class: 'campo' }, 'Estado', h('select', { onchange: set('estado') }, opciones(S.me.estados, f.estado, 'Todos'))),
    h('label', { class: 'campo' }, 'Alertas', h('select', { onchange: set('alerta') },
      h('option', { value: '' }, 'Todas'),
      h('option', { value: 'rojo', selected: f.alerta === 'rojo' }, 'Solo rojas'),
      h('option', { value: 'con', selected: f.alerta === 'con' }, 'Rojas y ámbar'),
      h('option', { value: 'sin', selected: f.alerta === 'sin' }, 'Sin alertas'),
      codigos.map((c) => h('option', { value: `code:${c}`, selected: f.alerta === `code:${c}` }, ALERTAS_NOMBRE[c] || c)))),
    f.guia ? h('label', { class: 'campo' }, 'Guía', h('button', { class: 'btn sec', type: 'button', onclick: () => { f.guia = ''; pintarCasos(); } }, `${f.guia} ✕`)) : null,
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.superAsp, onchange: set('superAsp') }), 'Solo Super ASP'),
    h('label', { class: 'check' }, h('input', { type: 'checkbox', checked: f.cerrados, onchange: intentar(async (ev) => { f.cerrados = ev.target.checked; await cargarCasos(); pintarTabla(); }) }), 'Incluir cerrados'),
  ));
  v.append(h('div', { class: 'barra' },
    h('label', { class: 'campo' }, 'Agrupar por', h('select', { onchange: (ev) => { S.grupo = ev.target.value; S.colapsados.clear(); pintarTabla(); } },
      [['', 'Sin agrupar'], ['tienda', 'Tienda'], ['opcion', 'Solución'], ['estado', 'Estado'], ['nivel', 'Alerta'], ['guia', 'Guía'], ['parte', 'Parte']]
        .map(([k, t]) => h('option', { value: k, selected: S.grupo === k }, t)))),
    h('label', { class: 'campo' }, 'Ordenar por', h('select', { onchange: (ev) => { S.orden = ev.target.value; pintarTabla(); } },
      [['prioridad', 'Prioridad'], ['limite', 'Días al límite de fill rate'], ['eta', 'Días a la ETA'], ['edad', 'Edad (más antiguos)'], ['edadDespacho', 'Edad al despacho (mayor primero)'], ['tienda', 'Tienda']]
        .map(([k, t]) => h('option', { value: k, selected: S.orden === k }, t)))),
    h('span', { class: 'separa' }),
    S.me.ver_precios ? h('a', { class: 'btn sec', href: '/api/export/cases.csv' }, 'Exportar CSV') : null,
  ));
  v.append(h('p', { class: 'resumen', id: 'resumen' }));
  v.append(h('div', { class: 'leyenda' },
    h('span', {}, h('i', { class: 'l-ventana' }), 'Ventana de fill rate (ETA a ETA + tolerancia)'),
    h('span', {}, h('i', { class: 'l-hoy' }), 'Hoy'),
    h('span', {}, h('i', { class: 'l-llegada' }), 'Llegada'),
    h('span', {}, h('i', { class: 'l-despacho' }), 'Despacho posible: verde con margen, ámbar el último día, rojo fuera de la ventana'),
  ));
  v.append(h('div', { class: `lienzo${S.sel ? ' con-detalle' : ''}`, id: 'lienzo' },
    h('div', { class: 'marco', id: 'marco' }), S.sel ? h('aside', { class: 'detalle', id: 'detalle' }) : null));
  pintarTabla();
  if (S.sel) abrirDetalle(S.sel);
}

function carril(c) {
  const ini = -4; const fin = 17; const ancho = fin - ini;
  const pos = (iso) => { const d = diasEntre(hoyISO(), iso); return { pct: Math.min(100, Math.max(0, ((d - ini) / ancho) * 100)), fuera: d < ini || d > fin }; };
  const el = h('div', { class: 'carril' }, h('div', { class: 'eje' }));
  if (c.eta_jenny && c.limite_fr) {
    const a = pos(c.eta_jenny); const b = pos(c.limite_fr);
    const w = h('div', { class: 'ventana' }); w.style.left = `${a.pct}%`; w.style.width = `${Math.max(2, b.pct - a.pct)}%`; el.append(w);
  }
  const hoy = h('div', { class: 'hoy' }); hoy.style.left = `${pos(hoyISO()).pct}%`; el.append(hoy);
  if (c.llegada && c.llegada_fuente !== 'En inventario') {
    const p = pos(c.llegada); const m = h('div', { class: `llegada${p.fuera ? ' fuera' : ''}` }); m.style.left = `${p.pct}%`; el.append(m);
  }
  if (c.despacho_posible) {
    const p = pos(c.despacho_posible);
    let cls = 'despacho';
    if (c.fecha_despacho) cls += ' hecho';
    else if (c.limite_fr && c.despacho_posible > c.limite_fr) cls += ' rojo';
    else if (c.limite_fr && c.despacho_posible === c.limite_fr) cls += ' ambar';
    const m = h('div', { class: `${cls}${p.fuera ? ' fuera' : ''}` }); m.style.left = `${p.pct}%`; el.append(m);
  }
  el.title = `Llegada: ${fechaLarga(c.llegada)} (${c.llegada_fuente})\nDespacho ${c.fecha_despacho ? 'realizado' : 'posible'}: ${fechaLarga(c.despacho_posible)}\nVentana de fill rate: ${fechaLarga(c.eta_jenny)} a ${fechaLarga(c.limite_fr)}`;
  return el;
}

function filaCaso(c) {
  const est = c.source_status === 'cerrado'
    ? h('span', { class: 'etiqueta cerrado' }, `Cerrado ${fecha(c.closed_on)}`)
    : h('span', { class: `etiqueta${c.estado === 'Despachado' ? ' despachado' : ''}` }, c.estado);
  const dias = c.dias_eta;
  return h('tr', {
    class: `fila${S.sel === c.key_notif ? ' activa' : ''}${c.source_status === 'cerrado' ? ' cerrado' : ''}`,
    tabindex: 0, dataset: { key: c.key_notif },
    onclick: () => seleccionar(c.key_notif),
    onkeydown: (ev) => { if (ev.key === 'Enter') seleccionar(c.key_notif); },
  },
  h('td', { class: 'nivel' }, h('span', { class: `punto ${c.nivel}`, title: c.alerts.map((a) => a.text).join('\n') || 'Sin alertas' })),
  h('td', {}, h('span', { class: 'caso' }, c.key_notif), c.super_asp ? [' ', h('span', { class: 'etiqueta super' }, 'Super ASP')] : null,
    c.responsable ? h('span', { class: 'sub' }, c.responsable) : null),
  h('td', { class: 'tienda' }, c.list_name),
  h('td', {}, h('span', { class: 'parte' }, c.assembly), h('span', { class: 'sub desc', title: c.description || '' }, c.description)),
  h('td', {}, h('span', { class: `etiqueta${c.option_short === 'OH' ? ' oh' : ''}` }, c.option_short),
    c.compra_miami ? h('span', { class: 'sub' }, `Miami comprada ${fecha(c.compra_miami.fecha_compra)}`) : null),
  h('td', { class: 'fecha' }, fecha(c.eta_jenny),
  h('span', { class: `sub${dias !== null && dias < 0 ? ' dias-neg' : ''}` }, dias === null ? '' : dias === 0 ? 'hoy' : dias > 0 ? `en ${dias} d` : `hace ${-dias} d`)),
  h('td', {}, carril(c)),
  h('td', { class: 'fecha' }, fecha(c.despacho_posible), h('span', { class: 'sub' }, c.fecha_despacho ? 'despachado' : c.llegada_fuente)),
  h('td', { class: 'num c-edad' }, c.edad ?? '—', c.edad_despacho !== null ? h('span', { class: 'sub' }, `${c.edad_despacho} al desp.`) : null),
  h('td', {}, est),
  h('td', { class: 'num c-precio' }, dinero(c.precio, c.moneda), c.precio !== null ? h('span', { class: 'sub' }, c.tipo_precio) : null),
  );
}

function pintarTabla() {
  const lista = filtrados().sort(ORDENES[S.orden]);
  const rojas = lista.filter((c) => c.nivel === 'rojo').length;
  const valor = lista.reduce((s, c) => s + (c.pendiente && c.precio ? c.precio : 0), 0);
  const res = vaciar($('#resumen'));
  res.append(h('b', {}, lista.length), ` líneas de ${S.casos.length}`, ' | ', h('b', {}, rojas), ' con alerta roja',
    ' | ', h('b', {}, new Set(lista.map((c) => c.notifctn)).size), ' reparaciones', ...(S.me.ver_precios ? [' | valor pendiente ', h('b', {}, dinero(valor, lista[0]?.moneda))] : []),
    S.kgbFecha ? ` | KGB del ${fecha(S.kgbFecha)}` : ' | sin KGB cargado');
  const marco = vaciar($('#marco'));
  if (!S.casos.length) {
    marco.append(h('div', { class: 'vacio' }, 'Aún no hay casos. Cargue el backlog del día en la pestaña Cargas.'));
    return;
  }
  const tbody = h('tbody');
  const cols = 11;
  if (S.grupo) {
    const grupos = new Map();
    for (const c of lista) { const k = GRUPOS[S.grupo](c) || '—'; if (!grupos.has(k)) grupos.set(k, []); grupos.get(k).push(c); }
    for (const [k, filas] of grupos) {
      const r = filas.filter((c) => c.nivel === 'rojo').length;
      const val = filas.reduce((s, c) => s + (c.pendiente && c.precio ? c.precio : 0), 0);
      const colapsado = S.colapsados.has(k);
      tbody.append(h('tr', { class: 'grupo', onclick: () => { colapsado ? S.colapsados.delete(k) : S.colapsados.add(k); pintarTabla(); } },
        h('td', { colspan: cols }, `${colapsado ? '▸' : '▾'} ${k}`,
          h('span', { class: 'cuenta' }, `${filas.length} líneas`),
          r ? h('span', { class: 'cuenta roja' }, `${r} en rojo`) : null,
          val ? h('span', { class: 'cuenta' }, dinero(val, filas[0].moneda)) : null)));
      if (!colapsado) filas.forEach((c) => tbody.append(filaCaso(c)));
    }
  } else {
    lista.forEach((c) => tbody.append(filaCaso(c)));
  }
  marco.append(h('table', {},
    h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', {}, 'Caso'), h('th', {}, 'Tienda'), h('th', {}, 'Parte'),
      h('th', {}, 'Solución'), h('th', {}, 'ETA Apple'), h('th', { title: 'Tres semanas: 4 días atrás y 17 adelante' }, 'Carril'),
      h('th', {}, 'Despacho'), h('th', { class: 'num c-edad' }, 'Edad'), h('th', {}, 'Estado'), h('th', { class: 'num c-precio' }, 'Precio'))),
    tbody));
  if (!lista.length) marco.append(h('div', { class: 'vacio' }, 'Ningún caso coincide con los filtros.'));
}

function seleccionar(key) {
  const ya = !!S.sel;
  S.sel = key;
  if (!ya) { pintarCasos(); return; }
  document.querySelectorAll('tr.fila').forEach((tr) => tr.classList.toggle('activa', tr.dataset.key === key));
  abrirDetalle(key);
}

const dato = (label, ...val) => [h('dt', {}, label), h('dd', {}, ...val)];
const CAMPOS = {
  estado: 'Estado', responsable: 'Responsable', fecha_recepcion: 'Recepción', fecha_despacho: 'Despacho',
  fecha_objetivo: 'Fecha a comunicar', propuesta: 'Propuesta', aprobacion_kam: 'Aprobación KAM',
  kam_solicitado: 'ACM solicitado', kam_respuesta: 'ACM respondió',
};

async function abrirDetalle(key) {
  const panel = $('#detalle');
  if (!panel) return;
  let det;
  try { det = await api('GET', `/api/cases/${encodeURIComponent(key)}`); } catch (e) { aviso(e.message, true); return; }
  const c = det.caso;
  vaciar(panel);
  panel.append(h('header', {},
    h('div', {}, h('h2', {}, `Notificación ${c.notifctn}, línea ${String(c.line_item).padStart(2, '0')}`),
      h('p', {}, `${c.list_name} (Ship-to ${c.ship_to})`)),
    h('button', { class: 'cerrar', type: 'button', 'aria-label': 'Cerrar detalle', onclick: () => { S.sel = null; pintarCasos(); } }, '✕')));

  if (c.alerts.length) {
    panel.append(h('div', { class: 'bloque' }, h('h3', {}, 'Alertas'),
      c.alerts.map((a) => h('div', { class: `alerta ${a.level}` }, a.text))));
  }

  panel.append(h('div', { class: 'bloque' }, h('h3', {}, 'Fechas'), h('dl', { class: 'datos' },
    dato('ETA de Apple', fechaLarga(c.eta_jenny), c.eta_changes ? ` (inicial ${fechaLarga(c.eta_first)}, ${c.eta_changes} cambio${c.eta_changes > 1 ? 's' : ''})` : ''),
    dato('Llegada', fechaLarga(c.llegada), ` (${c.llegada_fuente})`),
    dato(c.fecha_despacho ? 'Despachado' : 'Despacho posible', fechaLarga(c.despacho_posible)),
    dato('Límite fill rate', fechaLarga(c.limite_fr)),
    dato('Fecha para la tienda', fechaLarga(c.fecha_cliente), c.fecha_comunicada ? ` (última comunicada: ${fechaLarga(c.fecha_comunicada)})` : ''),
    dato('Edad', c.edad === null ? '—' : `${c.edad} días desde la creación`),
    dato(c.fecha_despacho ? 'Edad al despacho' : 'Edad al despacho (proyectada)', c.edad_despacho === null ? '—' : `${c.edad_despacho} días`),
  )));

  const g = {};
  const campo = (label, el, ancho) => h('label', { class: `campo${ancho ? ' ancho' : ''}` }, label, el);
  g.estado = h('select', {}, S.me.estados.map((e) => h('option', { value: e, selected: e === c.estado }, e)));
  g.responsable = h('input', { value: c.responsable || '', maxlength: 80 });
  g.fecha_recepcion = h('input', { type: 'date', value: c.fecha_recepcion || '' });
  g.fecha_despacho = h('input', { type: 'date', value: c.fecha_despacho || '' });
  g.fecha_objetivo = h('input', { type: 'date', value: c.fecha_objetivo || '' });
  g.propuesta = h('select', {}, S.me.propuestas.map((p) => h('option', { value: p, selected: p === (c.propuesta || '') }, p || 'Sin cambio')));
  g.aprobacion_kam = h('select', {}, S.me.aprobacion.map((p) => h('option', { value: p, selected: p === c.aprobacion_kam }, p)));
  const editable = puedeEditar();
  Object.values(g).forEach((el) => { el.disabled = !editable; });
  panel.append(h('div', { class: 'bloque' }, h('h3', {}, 'Gestión'),
    h('div', { class: 'form' },
      campo('Estado', g.estado), campo('Responsable', g.responsable),
      campo('Fecha de recepción', g.fecha_recepcion), campo('Fecha de despacho', g.fecha_despacho),
      campo('Fecha a comunicar (opcional)', g.fecha_objetivo), campo('Cambio de solución propuesto', g.propuesta),
      campo('Aprobación del Apple Care Manager', g.aprobacion_kam),
      c.kam_solicitado ? h('p', { class: 'nota ancho' }, `Solicitada ${fechaLarga(c.kam_solicitado)}${c.kam_respuesta ? `, respondida ${fechaLarga(c.kam_respuesta)}` : ''}.`) : null),
    editable ? h('div', { class: 'acciones' }, h('button', {
      class: 'btn', type: 'button', onclick: intentar(async () => {
        const body = {};
        for (const [k, el] of Object.entries(g)) body[k] = el.value;
        const r = await api('PATCH', `/api/cases/${encodeURIComponent(key)}`, body);
        aviso(r.cambios ? 'Cambios guardados.' : 'No había cambios.');
        await cargarCasos(); pintarTabla(); abrirDetalle(key);
      }),
    }, 'Guardar cambios')) : null));

  const kgbTexto = {
    guia_parte: 'La guía está en el KGB y trae esta parte.', guia_sustituto: 'La guía está en el KGB y trae un sustituto.',
    guia_sin_parte: 'La guía está en el KGB, pero sin esta parte.', guia_no_kgb: 'La guía no aparece en el KGB cargado.',
    sin_guia: 'Sin guía en los comentarios de Apple.',
  }[c.kgb_match];
  panel.append(h('div', { class: 'bloque' }, h('h3', {}, 'Tránsito (KGB)'),
    h('p', { class: 'nota' }, c.tracking_list.length ? `Guía ${c.tracking_list.join(', ')}. ` : '', kgbTexto),
    det.kgb_guia.length ? h('table', { class: 'mini' },
      h('thead', {}, h('tr', {}, ['Guía', 'Parte', 'Unid.', 'EDD', 'Evento', 'SLA'].map((t) => h('th', {}, t)))),
      h('tbody', {}, det.kgb_guia.map((r) => h('tr', {}, h('td', {}, r.tracking), h('td', {}, r.prod_id), h('td', {}, r.qty),
        h('td', {}, fecha(r.edd)), h('td', {}, r.current_event || r.status), h('td', {}, r.sla_ind))))) : null,
    det.kgb_parte ? h('p', { class: 'nota' }, `Esta parte en todo el KGB: ${det.kgb_parte.qty} unidades, primera llegada ${fechaLarga(det.kgb_parte.edd)}${det.kgb_parte.guides.length ? `, guías ${det.kgb_parte.guides.join(', ')}` : ''}.`) : null));
  panel.append(bloqueAbastecimiento(c));
  if (S.me.ver_miami) panel.append(bloqueMiami(c));

  panel.append(h('div', { class: 'bloque' }, h('h3', {}, 'Datos de Apple (solo lectura)'), h('dl', { class: 'datos' },
    dato('Solución', c.option_jenny || '—'),
    dato('Comentarios', c.comments_jenny || '—'),
    dato('Notas', c.notes_jenny || '—'),
    c.subs_list.length ? dato('Sustitutos en comentarios', c.subs_list.join(', ')) : null,
    dato('Parte', `${c.assembly} ${c.description || ''}`),
    dato('Sustituto oficial', c.sustituto_pd || 'No tiene (Parts Data)'),
    dato('Equipo', c.product_description || c.material_desc || '—'),
    dato('Garantía', `${c.warranty || '—'} (${c.tipo_precio})`),
    S.me.ver_precios ? dato('Valor (stocking)', dinero(c.precio, c.moneda)) : null,
    c.precio !== null ? dato('Paga la tienda', c.precio_tienda === null ? 'Sin precio exchange' : dinero(c.precio_tienda, c.moneda)) : null,
    c.precio !== null ? dato('Paga Apple', c.precio_apple === null ? '—' : dinero(c.precio_apple, c.moneda)) : null,
    Object.keys(c.precios_especiales).length ? dato('Precios por tipo de reparación', Object.entries(c.precios_especiales)
      .map(([k, v]) => `${{ battery: 'solo batería', display: 'solo pantalla', display_backglass: 'pantalla y cristal trasero', backglass: 'solo cristal trasero', addon: 'adicional' }[k]}: ${dinero(v, c.moneda)}`).join('; ')) : null,
    dato('Estrategia', `${c.repair_strategy || '—'}${c.super_asp ? ', se repara en el Super ASP' : ''}`),
    dato('PO de la tienda', c.po_number || '—'),
    dato('Serial', c.serial_no || '—'),
    dato('Creado', fechaLarga(c.created_on)),
    dato('En el reporte desde', fechaLarga(c.first_seen)),
  )));

  const esAcm = S.me.role === 'acm';
  const texto = h('textarea', { maxlength: 2000, placeholder: esAcm ? 'Nota para el equipo de Ingram…' : 'Cómo se está solucionando este caso…' });
  const vis = h('select', { class: 'solo', title: 'Quién puede leer este comentario' },
    h('option', { value: 'interno' }, 'Interno (solo el equipo)'), h('option', { value: 'compartido' }, 'Compartido con Apple'));
  panel.append(h('div', { class: 'bloque' }, h('h3', {}, esAcm ? 'Notas' : 'Comentarios'),
    S.me.puede_comentar ? [texto, h('div', { class: 'acciones' }, esAcm ? null : vis, h('button', {
      class: 'btn sec', type: 'button', onclick: intentar(async () => {
        if (!texto.value.trim()) return;
        await api('POST', `/api/cases/${encodeURIComponent(key)}/comments`, { text: texto.value, visibilidad: vis.value });
        aviso(esAcm ? 'Nota agregada.' : 'Comentario agregado.'); abrirDetalle(key);
      }),
    }, esAcm ? 'Agregar nota' : 'Agregar comentario'))] : null,
    det.comentarios.length ? det.comentarios.map((m) => h('div', { class: 'comentario' }, h('small', {}, `${m.username}, ${m.at.replace('T', ' ')}`,
      esAcm ? null : h('span', { class: `vis ${m.visibilidad}` }, m.visibilidad === 'compartido' ? 'Compartido con Apple' : 'Interno')), m.text))
      : h('p', { class: 'nota' }, 'Sin comentarios todavía.')));

  const hist = [
    ...det.historial.map((x) => ({ at: x.file_date, quien: 'Archivo de Apple', txt: `${x.field}: ${x.old ?? '—'} → ${x.new ?? '—'}` })),
    ...det.gestion.map((x) => {
      const d = JSON.parse(x.detail || '{}');
      const partes = Object.entries(d).filter(([k]) => k !== 'origen').map(([k, v]) => `${CAMPOS[k] || k}: ${v.antes ?? '—'} → ${v.ahora ?? '—'}`);
      return { at: x.at, quien: x.username, txt: partes.join('; ') + (d.origen ? ` (${d.origen})` : '') };
    }),
  ].sort((a, b) => (a.at < b.at ? 1 : -1));
  panel.append(h('div', { class: 'bloque' }, h('h3', {}, 'Historial'),
    hist.length ? hist.map((x) => h('div', { class: 'comentario' }, h('small', {}, `${x.quien}, ${x.at.replace('T', ' ')}`), x.txt))
      : h('p', { class: 'nota' }, 'Sin cambios registrados.')));
}

// ------------------------------------------------------------------ guías
async function pintarGuias() {
  const v = vaciar($('#v-guias'));
  const r = await api('GET', '/api/guides');
  const buscar = h('input', {
    type: 'search', value: S.guiaQ || '', placeholder: 'Guía, caso, parte o tienda',
    oninput: (ev) => { S.guiaQ = ev.target.value; pintarFilasGuias(r.guias, cuerpo, resumen); },
  });
  const resumen = h('p', { class: 'resumen' });
  const cuerpo = h('tbody');
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Guías de Apple'),
    h('p', {}, 'Cada guía agrupa los casos que viajan en el mismo envío. Cuando llegue, registre la llegada una sola vez y se marcan todos sus casos como recibidos. ',
      `El cruce usa el número de guía de los comentarios del Apple Care Manager contra el KGB del ${r.kgb_fecha ? fecha(r.kgb_fecha) : '(sin cargar)'}.`),
    h('div', { class: 'barra' }, h('label', { class: 'campo' }, 'Buscar', buscar))));
  v.append(resumen);
  if (!r.guias.length) { v.append(h('div', { class: 'vacio' }, 'No hay guías en los casos abiertos.')); return; }
  v.append(h('div', { class: 'marco' }, h('table', {},
    h('thead', {}, h('tr', {}, ['Guía', 'Casos pendientes', 'Partes', 'Tiendas', 'En KGB', 'Llegada KGB', 'Evento KGB', 'ETA Apple más cercana', 'Retraso', 'Llegada registrada', ''].map((t) => h('th', {}, t)))),
    cuerpo)));
  pintarFilasGuias(r.guias, cuerpo, resumen);
}

function pintarFilasGuias(guias, cuerpo, resumen) {
  const q = (S.guiaQ || '').trim().toLowerCase();
  const lista = q ? guias.filter((g) => g.buscar.includes(q)) : guias;
  vaciar(resumen).append(h('b', {}, lista.length), ` de ${guias.length} guías`);
  vaciar(cuerpo).append(...lista.map((g) => {
    const f = h('input', { type: 'date', class: 'solo', value: hoyISO() });
    return h('tr', {},
      h('td', {}, h('button', { class: 'btn sec', type: 'button', title: 'Ver sus casos', onclick: () => { S.f.guia = g.guia; S.sel = null; mostrar('casos'); } }, g.guia)),
      h('td', { class: 'num' }, `${g.pendientes} / ${g.casos}`, g.rojas ? h('span', { class: 'sub dias-neg' }, `${g.rojas} en rojo`) : null),
      h('td', { class: 'num' }, g.partes), h('td', { class: 'num' }, g.tiendas),
      h('td', {}, g.en_kgb ? h('span', { class: 'badge-si' }, `Sí (${g.kgb_lineas})`) : h('span', { class: 'badge-no' }, 'No')),
      h('td', { class: 'fecha' }, fecha(g.kgb_edd)), h('td', {}, g.kgb_evento || '—'),
      h('td', { class: 'fecha' }, fecha(g.eta_min)),
      h('td', {}, g.retrasada ? h('span', { class: 'badge-no', title: 'La EDD del KGB pasa la ETA de Apple o Apple reporta demora' }, 'Retrasada')
        : g.fuera_sla ? h('span', { class: 'sub', title: 'Apple la marca Out of SLA en el KGB, pero llega antes de la ETA' }, 'Fuera de SLA Apple') : '—'),
      h('td', { class: 'fecha' }, g.llegada ? fecha(g.llegada) : '—'),
      h('td', {}, puedeEditar() && g.pendientes ? h('div', { class: 'acciones' }, f, h('button', {
        class: 'btn', type: 'button', onclick: intentar(async () => {
          const res = await api('POST', `/api/guides/${encodeURIComponent(g.guia)}/arrival`, { fecha: f.value });
          aviso(`Llegada registrada: ${res.actualizados} de ${res.casos} casos actualizados.`);
          await cargarCasos(); pintarGuias();
        }),
      }, 'Registrar llegada')) : null));
  }));
  if (!lista.length) cuerpo.append(h('tr', {}, h('td', { colspan: 11, class: 'vacio' }, 'Ninguna guía coincide con la búsqueda.')));
}

// ------------------------------------------------------------------ correos
async function pintarCorreos() {
  const v = vaciar($('#v-correos'));
  const r = await api('GET', '/api/emails');
  const conCorreo = r.grupos.filter((g) => g.email).length;
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Correo diario por tienda'),
    h('p', {}, 'Un correo por tienda con tres bloques: por despachar (con fecha, solo repuestos ya en bodega), en gestión (sin fecha, para no comprometer ninguna) y despachados desde el último correo. Al copiar o descargar, la app guarda la fecha comunicada.'),
    r.grupos.length > conCorreo ? h('p', { class: 'nota' }, `${r.grupos.length - conCorreo} tiendas no tienen correo: un administrador las asocia con el archivo de tiendas en Administración → Correos de las tiendas.`) : null,
    h('div', { class: 'barra' },
      h('p', { class: 'resumen' }, h('b', {}, r.grupos.length), ' tiendas, ', h('b', {}, conCorreo), ' con correo registrado.'),
      h('span', { class: 'separa' }),
      puedeEditar() && conCorreo ? h('button', {
        class: 'btn', type: 'button', onclick: intentar(async () => {
          const resp = await api('POST', '/api/emails-lote', undefined, true);
          descargar(await resp.blob(), `correos_${r.fecha}.zip`);
          aviso(`Se descargaron ${conCorreo} borradores. Descomprima y ábralos con Outlook para enviarlos.`);
        }),
      }, 'Descargar todos (.zip)') : null)));
  if (!r.grupos.length) { v.append(h('div', { class: 'vacio' }, 'No hay casos pendientes ni despachos recientes.')); return; }
  r.grupos.forEach((g) => v.append(tarjetaCorreo(g)));
}

function tarjetaCorreo(g) {
  const correo = h('input', { class: 'solo', type: 'email', value: g.email || '', placeholder: 'correo@tienda.com', size: 30 });
  const registrar = (formato) => api('POST', `/api/emails/${encodeURIComponent(g.ship_to)}`, { formato }, formato === 'eml');
  const n = g.conteo;
  return h('article', { class: 'correo' },
    h('header', {}, h('h3', {}, g.tienda),
      h('span', { class: 'sub' }, `Ship-to ${g.ship_to}: ${n.fecha} por despachar, ${n.gestion} en gestión, ${n.despachados} despachados`),
      h('span', { class: 'separa' }),
      g.email ? null : h('span', { class: 'sin-correo' }, 'Sin correo registrado'),
      puedeEditar() ? [correo, h('button', {
        class: 'btn sec', type: 'button', onclick: intentar(async () => {
          await api('PUT', `/api/shipto/${encodeURIComponent(g.ship_to)}`, { email: correo.value }); aviso('Correo de la tienda guardado.'); pintarCorreos();
        }),
      }, 'Guardar correo')] : null),
       h('pre', {}, `${g.cc ? `CC: ${g.cc}\n` : ''}Asunto: ${g.subject}\n\n${g.body}`),
    puedeEditar() ? h('div', { class: 'acciones' },
      h('button', {
        class: 'btn sec', type: 'button', onclick: intentar(async () => {
          await navigator.clipboard.writeText(g.body); await registrar('texto'); aviso('Texto copiado. Fecha comunicada registrada.');
        }),
      }, 'Copiar texto'),
      h('button', {
        class: 'btn', type: 'button', onclick: intentar(async () => {
          const resp = await registrar('eml');
          const nombre = (resp.headers.get('content-disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'correo.eml';
          descargar(await resp.blob(), nombre); aviso('Borrador descargado. Ábralo con Outlook para enviarlo.');
        }),
      }, 'Descargar borrador')) : null);
}

// ------------------------------------------------------------------ indicadores
async function pintarIndicadores() {
  const v = vaciar($('#v-indicadores'));
  const k = await api('GET', '/api/kpis');
  const fr = k.fill_rate;
  const cifra = (valor, label, cls) => h('div', { class: `cifra${cls ? ` ${cls}` : ''}` }, h('b', {}, valor), h('span', {}, label));
  v.append(h('div', { class: 'tira' },
    cifra(fr.valor === null ? '—' : `${fr.valor}%`, fr.valor === null ? 'Fill rate backlog: registre fechas de despacho para calcularlo' : `Fill rate backlog (${fr.a_tiempo} de ${fr.evaluadas} líneas a tiempo)`, 'principal'),
    cifra(k.proyeccion.en_riesgo, `en riesgo de ${k.proyeccion.vencen_7d} que vencen en 7 días`, k.proyeccion.en_riesgo ? 'alerta-cifra' : ''),
    cifra(k.pendientes_despacho, `líneas por despachar (${k.reparaciones} reparaciones)`),
    S.me.ver_precios ? cifra(dinero(k.valor.total, k.moneda), k.valor.sin_precio ? `valor pendiente (${k.valor.sin_precio} líneas sin precio)` : 'valor pendiente') : null,
    cifra(k.oh.total, `OH por despachar (${k.oh.demorados} demorados)`, k.oh.demorados ? 'alerta-cifra' : ''),
    cifra(k.kam.pendientes, k.kam.dias_prom === null ? 'esperando al Apple Care Manager' : `esperando al Apple Care Manager (${k.kam.dias_prom} días prom.)`),
  ));
  v.append(h('p', { class: 'nota' }, `Fill rate: líneas cuyo límite (ETA + tolerancia) venció desde el ${fecha(fr.desde)}; a tiempo si la fecha de despacho registrada es igual o anterior al límite.`,
    fr.sin_registro ? ` ${fr.sin_registro} líneas salieron del reporte sin fecha de despacho y no se cuentan.` : ''));

  const ti = k.tiendas;
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Tiendas en backlog'),
    h('p', {}, 'Tiendas (AASP) con al menos una línea por despachar. El cliente es el dominio del correo de la tienda (archivo de tiendas); si la tienda no está en ese archivo, la primera palabra de su nombre (ISHOP, MAC, WODEN…).'),
    h('div', { class: 'tira' },
      cifra(ti.con_backlog, 'tiendas con backlog'),
      cifra(ti.clientes, 'clientes'),
      cifra(ti.con_rojas, 'tiendas con líneas en rojo', ti.con_rojas ? 'alerta-cifra' : ''),
      cifra(ti.top10_pct === null ? '—' : `${ti.top10_pct}%`, 'de las líneas está en las 10 tiendas con más casos'),
      cifra(ti.entraron === null ? '—' : `+${ti.entraron} / −${ti.salieron}`, 'tiendas que entraron / salieron en la última carga'))));    
  v.append(await panelKgbColombia());

  const barras = (filas, max) => h('div', { class: 'barras' }, filas.flatMap(([label, n, roja]) => {
    const b = h('div', { class: `barra-v${roja ? ' roja' : ''}` }); b.style.width = `${max ? (n / max) * 100 : 0}%`;
    return [h('span', {}, label), h('div', {}, b), h('span', { class: 'num' }, n)];
  }));
  const edad = Object.entries(k.edad);
  const flujo = k.flujo;
  const maxFlujo = Math.max(1, ...flujo.flatMap((f) => [f.nuevas, f.salieron]));
  v.append(h('div', { class: 'rejilla' },
    h('div', { class: 'panel' }, h('h2', {}, 'Flujo diario'),
      h('p', {}, 'Casos que entran y salen del reporte en cada carga. Si salen más de los que entran, el backlog baja.'),
      flujo.length ? h('table', { class: 'mini' },
        h('thead', {}, h('tr', {}, ['Archivo', 'Nuevas', 'Salieron', 'Neto', 'Abiertas', 'Tiendas'].map((t) => h('th', {}, t)))),
        h('tbody', {}, flujo.map((f) => h('tr', {}, h('td', {}, fecha(f.fecha)), h('td', { class: 'num' }, f.nuevas),
          h('td', { class: 'num' }, f.salieron), h('td', { class: `num${f.neto > 0 ? ' dias-neg' : ''}` }, f.neto > 0 ? `+${f.neto}` : f.neto),
          h('td', { class: 'num' }, f.abiertas), h('td', { class: 'num' }, f.tiendas))))) : h('p', { class: 'nota' }, 'Se llena a partir de la segunda carga.'),
      flujo.length > 1 ? barras(flujo.slice(-7).map((f) => [`${fecha(f.fecha)} salieron`, f.salieron]), maxFlujo) : null),
    h('div', { class: 'panel' }, h('h2', {}, 'Edad de lo pendiente'),
      h('p', {}, 'Días desde la creación del caso.'),
      barras(edad.map(([b, n]) => [`${b} días`, n, b === '31+']), Math.max(...edad.map(([, n]) => n)))),
          h('div', { class: 'panel' }, h('h2', {}, 'Edad al despacho'),
      h('p', {}, 'En cuántos días se soluciona cada caso: de la creación al despacho real o, si aún no sale, al despacho posible.'),
      h('div', { class: 'tira' },
        cifra(k.edad_despacho.proyectada_prom === null ? '—' : `${k.edad_despacho.proyectada_prom} d`, 'proyectada (pendientes)'),
        cifra(k.edad_despacho.real_prom === null ? '—' : `${k.edad_despacho.real_prom} d`, `real (${k.edad_despacho.despachadas} despachadas)`)),
      barras(Object.entries(k.edad_despacho.rangos).map(([b, n]) => [`${b} días`, n, b === '31+']),
        Math.max(1, ...Object.values(k.edad_despacho.rangos)))),
    h('div', { class: 'panel' }, h('h2', {}, 'Por solución'),
      h('p', {}, 'Abiertas por tipo de solución, edad al despacho proyectada de lo pendiente y ciclo real de lo despachado.'),
      h('table', { class: 'mini' }, h('thead', {}, h('tr', {}, ['Solución', 'Abiertas', 'Proyectado', 'Despachadas', 'Ciclo real'].map((t) => h('th', {}, t)))),
        h('tbody', {}, k.por_opcion.map((o) => h('tr', {}, h('td', {}, o.opcion), h('td', { class: 'num' }, o.abiertas),
                   h('td', { class: 'num' }, o.proyectado_prom === null ? '—' : `${o.proyectado_prom} d`),
          h('td', { class: 'num' }, o.despachadas), h('td', { class: 'num' }, o.ciclo_prom === null ? '—' : `${o.ciclo_prom} d`)))))),
    h('div', { class: 'panel' }, h('h2', {}, 'Confiabilidad de la ETA'),
      h('p', {}, 'Cuántas líneas abiertas han tenido cambio de ETA desde que entraron al reporte.'),
      h('div', { class: 'tira' }, cifra(k.eta.con_cambios, `de ${k.eta.abiertas} con cambio de ETA`),
        cifra(k.eta.corrimiento_prom === null ? '—' : `${k.eta.corrimiento_prom} d`, 'corrimiento promedio'))),
    h('div', { class: 'panel' }, h('h2', {}, 'Partes con más casos'),
      h('table', { class: 'mini' }, h('thead', {}, h('tr', {}, ['Parte', 'Descripción', 'Líneas', 'Rojas', 'Valor'].map((t) => h('th', {}, t)))),
        h('tbody', {}, k.top_partes.map((p) => h('tr', {}, h('td', { class: 'fecha' }, p.parte), h('td', {}, p.desc), h('td', { class: 'num' }, p.lineas),
          h('td', { class: 'num' }, p.rojas), h('td', { class: 'num' }, dinero(p.valor, k.moneda))))))),
    h('div', { class: 'panel' }, h('h2', {}, 'Tiendas con más casos'),
      h('table', { class: 'mini' }, h('thead', {}, h('tr', {}, ['Tienda', 'Líneas', 'Rojas', 'Valor'].map((t) => h('th', {}, t)))),
        h('tbody', {}, k.top_tiendas.map((p) => h('tr', {}, h('td', {}, p.desc), h('td', { class: 'num' }, p.lineas),
          h('td', { class: 'num' }, p.rojas), h('td', { class: 'num' }, dinero(p.valor, k.moneda))))))),
  ));
}

// ------------------------------------------------------------------ abastecimiento (bloque B)
const pct1 = (x) => (x === null || x === undefined ? '—' : `${Number(x).toLocaleString('es-CO', { maximumFractionDigits: 1 })} %`);
const pts1 = (x) => (x === null || x === undefined ? '—' : `${x > 0 ? '+' : ''}${Number(x).toLocaleString('es-CO', { maximumFractionDigits: 1 })} pts`);
const unid = (x) => (x ? Number(x).toLocaleString('es-CO', { maximumFractionDigits: 0 }) : '—');
const opcionesTexto = (o) => Object.entries(o || {}).map(([k, n]) => `${n} ${k}`).join(', ');

async function pintarPartes() {
  const v = vaciar($('#v-partes'));
  const r = await api('GET', `/api/supply?todas=${S.pTodas ? 1 : 0}`);
  const res = r.resumen; const sal = r.salidas || {}; const n = sal.conteo || {};
  const cifra = (valor, label, cls) => h('div', { class: `cifra${cls ? ` ${cls}` : ''}` }, h('b', {}, valor), h('span', {}, label));
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Abastecimiento por parte'),
    h('p', {}, `Demanda del backlog frente a lo que viene de Apple: KGB del ${r.kgb_fecha ? fecha(r.kgb_fecha) : '(sin cargar)'} y Open Orders del ${r.oo_fecha ? fecha(r.oo_fecha) : '(sin cargar)'}. `,
      `Una línea que sigue en Open Orders todavía no ha salido de Apple. La llegada de lo pedido se estima como la salida de Apple (Recovery ETA) + ${r.transito_dias} días; el parámetro está en Administración.`)));
  v.append(h('div', { class: 'tira' },
    cifra(res.sin_abasto, `partes sin pedido ni tránsito (${res.sin_abasto_casos} líneas del backlog)`, res.sin_abasto ? 'alerta-cifra' : ''),
    cifra(res.solo_miami, `partes que solo trae Miami (${res.solo_miami_casos} líneas)`),
    cifra(res.con_pedido, `de ${res.partes_con_casos} partes del backlog tienen pedido en Open Orders`),
    cifra(unid(res.oo_unidades), `unidades en Open Orders (${res.oo_lineas} líneas, ${res.oo_ordenes} órdenes)`),
    cifra(sal.desde ? (n.kgb || 0) + (n.otro_item || 0) + (n.sin_rastro || 0) : '—',
      sal.desde ? `líneas salieron de Open Orders del ${fecha(sal.desde)} al ${fecha(sal.hasta)}: ${n.kgb || 0} ya en el KGB, ${n.otro_item || 0} cambiaron de ítem, ${n.sin_rastro || 0} sin rastro`
        : 'líneas que salen de Open Orders (se ve desde la segunda carga)')));
  const buscar = h('input', { type: 'search', value: S.pQ || '', placeholder: 'Parte o descripción', oninput: (ev) => { S.pQ = ev.target.value; filas(); } });
  const filtro = h('select', { onchange: (ev) => { S.pFiltro = ev.target.value; filas(); } },
    [['', 'Todas'], ['alerta', 'Con alerta'], ['SIN_ABASTO', 'Sin pedido ni tránsito'], ['pedido', 'Con pedido en Open Orders']]
      .map(([k, t]) => h('option', { value: k, selected: (S.pFiltro || '') === k }, t)));
  const todas = h('input', { type: 'checkbox', checked: !!S.pTodas, onchange: intentar(async (ev) => { S.pTodas = ev.target.checked; await pintarPartes(); }) });
  const resumen = h('p', { class: 'resumen' });
  const cuerpo = h('tbody');
  const conInv = !!S.me.ver_miami;            // el inventario propio es dato interno de Ingram
  const columnas = conInv ? 11 : 10;
  v.append(h('div', { class: 'barra' }, h('label', { class: 'campo' }, 'Buscar', buscar), h('label', { class: 'campo' }, 'Mostrar', filtro),
    h('label', { class: 'check' }, todas, 'Incluir partes pedidas que no están en el backlog')));
  v.append(resumen);
  v.append(h('div', { class: 'marco' }, h('table', {},
    h('thead', {}, h('tr', {}, ['', 'Parte', 'Casos pendientes', conInv ? 'Inventario propio' : null, 'KGB con guía', 'KGB sin guía', 'Open Orders', 'Estado en Apple', 'Llegada estimada', 'Límite más cercano', 'Alerta']
      .filter((t) => t !== null).map((t) => h('th', {}, t)))), cuerpo)));

  function filas() {
    const q = (S.pQ || '').trim().toLowerCase(); const f = S.pFiltro || '';
    const lista = r.filas.filter((x) => {
      if (q && !`${x.parte} ${x.descripcion || ''} ${x.sustituto || ''}`.toLowerCase().includes(q)) return false;
      if (f === 'alerta' && !x.alertas.length) return false;
      if (f === 'SIN_ABASTO' && !x.alertas.some((a) => a.code === 'SIN_ABASTO')) return false;
      if (f === 'pedido' && !x.oo_qty) return false;
      return true;
    });
    vaciar(resumen).append(h('b', {}, lista.length), ` de ${r.filas.length} partes`);
    vaciar(cuerpo);
    for (const x of lista) {
      const detalle = h('tr', { class: 'parte-detalle', hidden: true }, h('td', { colspan: columnas }));
      cuerpo.append(h('tr', { class: 'parte-fila', title: 'Clic para ver los pedidos de esta parte', onclick: intentar(async () => {
        if (!detalle.hidden) { detalle.hidden = true; return; }
        detalle.hidden = false; await pintarDetalleParte(x.parte, detalle.firstChild);
      }) },
      h('td', { class: 'nivel' }, h('span', { class: `punto ${x.nivel}`, title: x.alertas.map((a) => a.text).join('\n') || 'Sin alertas' })),
      h('td', {}, h('span', { class: 'parte' }, x.parte), h('span', { class: 'sub desc', title: x.descripcion || '' }, x.descripcion),
        x.sustituto ? h('span', { class: 'sub' }, `Sustituto: ${x.sustituto}`) : null),
      h('td', { class: 'num' }, x.casos || '—', x.rojos ? h('span', { class: 'sub dias-neg' }, `${x.rojos} en rojo`) : null,
        x.casos ? h('span', { class: 'sub' }, opcionesTexto(x.opciones)) : null),
      conInv ? h('td', { class: 'num' }, r.inv_fecha ? unid(x.inventario) : '—') : null,
      h('td', { class: 'num' }, unid(x.kgb_con_guia), x.kgb_con_guia ? h('span', { class: 'sub' }, `EDD ${fecha(x.kgb_edd)}`) : null),
      h('td', { class: 'num' }, unid(x.kgb_sin_guia), x.salieron_al_kgb ? h('span', { class: 'sub' }, `${x.salieron_al_kgb} líneas recién salidas de Open Orders`) : null),
      h('td', { class: 'num' }, unid(x.oo_qty), x.oo_qty ? h('span', { class: 'sub' }, `${x.oo_lineas} líneas, ${x.oo_ordenes} órdenes`) : null),
      h('td', {}, x.oo_estado || '—', x.oo_sin_fecha ? h('span', { class: 'sub' }, `${unid(x.oo_sin_fecha)} unid. sin fecha`) : null),
      h('td', { class: 'fecha' }, fecha(x.oo_llegada), x.oo_salida ? h('span', { class: 'sub' }, `sale de Apple ${fecha(x.oo_salida)}`) : null),
      h('td', { class: 'fecha' }, fecha(x.limite)),
      h('td', {}, x.alertas.map((a) => h('div', { class: `alerta ${a.level}` }, a.text)), x.casos ? h('button', {
        class: 'btn sec', type: 'button', onclick: (ev) => { ev.stopPropagation(); S.f.q = x.parte; S.sel = null; mostrar('casos'); },
      }, 'Ver casos') : null)));
      cuerpo.append(detalle);
    }
    if (!lista.length) cuerpo.append(h('tr', {}, h('td', { colspan: columnas, class: 'vacio' }, 'Ninguna parte coincide.')));
  }
  filas();
  if (S.me.ver_miami) v.append(await panelInventario());
  if (S.me.ver_miami) v.append(await panelMiami());
}

async function pintarDetalleParte(parte, celda) {
  vaciar(celda).append(h('p', { class: 'nota' }, 'Cargando…'));
  const d = await api('GET', `/api/supply/${encodeURIComponent(parte)}`);
  vaciar(celda);
  celda.append(h('p', { class: 'nota' }, `${d.casos_pendientes} casos pendientes de esta parte${d.sustituto ? ` (se suma el sustituto ${d.sustituto})` : ''}. `,
    d.historia.length ? `Unidades en Open Orders por día: ${d.historia.map((x) => `${fecha(x.file_date)} ${x.unidades}`).join(' · ')}.` : ''));
  celda.append(d.open_orders.length ? h('table', { class: 'mini' },
    h('thead', {}, h('tr', {}, ['Orden', 'Ítem', 'Parte', 'Unid.', 'Estado', 'Recovery ETA (texto de Apple)', 'Sale de Apple', 'Llegada est.', 'Tipo', 'En Open Orders desde'].map((t) => h('th', {}, t)))),
    h('tbody', {}, d.open_orders.map((o) => h('tr', {}, h('td', {}, o.sales_ord), h('td', {}, o.item), h('td', {}, o.part), h('td', { class: 'num' }, o.remaining_qty),
      h('td', {}, o.status, o.bloqueo ? h('span', { class: 'sub' }, `Bloqueo ${o.bloqueo}`) : null), h('td', {}, o.recovery_raw || '—'),
      h('td', { class: 'fecha' }, fecha(o.salida_est)), h('td', { class: 'fecha' }, fecha(o.llegada_est)), h('td', {}, o.tipo), h('td', { class: 'fecha' }, fecha(o.first_seen))))))
    : h('p', { class: 'nota' }, 'No hay líneas de esta parte en Open Orders.'));
  if (d.salidas.length) {
    celda.append(h('p', { class: 'nota' }, 'Salieron de Open Orders en la última carga: ',
      d.salidas.map((s) => `${s.orden}/${s.item} (${s.cantidad} unid.): ${s.resultado}`).join('; '), '.'));
  }
}

function bloqueAbastecimiento(c) {
  const caja = h('div', { class: 'bloque' }, h('h3', {}, 'Abastecimiento (Open Orders de Apple)'), h('p', { class: 'nota' }, 'Cargando…'));
  intentar(async () => {
    const d = await api('GET', `/api/supply/${encodeURIComponent(c.assembly)}`);
    caja.lastChild.remove();
    const oo = d.open_orders; const total = oo.reduce((s, o) => s + (o.remaining_qty || 0), 0);
    const proxima = oo.find((o) => o.llegada_est);
    caja.append(h('p', { class: 'nota' }, oo.length
      ? `${total} unid. pedidas a Apple en ${oo.length} líneas. ${proxima ? `La primera saldría de Apple el ${fechaLarga(proxima.salida_est)} y llegaría hacia el ${fechaLarga(proxima.llegada_est)} (estimado).` : 'Ninguna tiene fecha de salida todavía.'} Hay ${d.casos_pendientes} casos pendientes de esta parte.`
      : `Esta parte no está en Open Orders. Hay ${d.casos_pendientes} casos pendientes de esta parte.`));
    if (oo.length) {
      caja.append(h('table', { class: 'mini' }, h('thead', {}, h('tr', {}, ['Orden', 'Unid.', 'Estado', 'Sale de Apple', 'Llegada est.'].map((t) => h('th', {}, t)))),
        h('tbody', {}, oo.slice(0, 6).map((o) => h('tr', {}, h('td', {}, `${o.sales_ord}/${o.item}`), h('td', { class: 'num' }, o.remaining_qty),
          h('td', {}, o.status), h('td', { class: 'fecha' }, fecha(o.salida_est)), h('td', { class: 'fecha' }, fecha(o.llegada_est)))))));
      if (oo.length > 6) caja.append(h('p', { class: 'nota' }, `Y ${oo.length - 6} líneas más en la pestaña Abastecimiento.`));
    }
  })();
  return caja;
}

async function panelKgbColombia() {
  const filas = await api('GET', '/api/kgb-colombia');
  const u = filas[0];
  const cifra = (valor, label) => h('div', { class: 'cifra' }, h('b', {}, valor), h('span', {}, label));
  const variacion = (x) => (x === undefined || x === null ? '—' : h('span', { class: x > 0 ? 'var-sube' : x < 0 ? 'var-baja' : '' }, pts1(x)));
  return h('div', { class: 'panel' }, h('h2', {}, 'KGB: lo que va a Colombia'),
    h('p', {}, 'Porcentaje de las líneas del KGB completo (todos los países) que tienen destino Colombia, y cuánto cambió frente al KGB anterior.'),
    u ? h('div', { class: 'tira' },
      cifra(pct1(u.pct_filas), `de las líneas del KGB del ${fecha(u.fecha)} van a Colombia (${u.filas_co} de ${u.filas})`),
      cifra(variacion(u.var_filas), 'frente al KGB anterior')) : h('p', { class: 'nota' }, 'Sin KGB cargado.'),
    filas.length ? h('table', { class: 'mini' },
      h('thead', {}, h('tr', {}, ['KGB', 'Líneas a Colombia', '% de líneas', 'Cambio', 'Guías a Colombia'].map((t) => h('th', {}, t)))),
      h('tbody', {}, filas.map((x) => h('tr', {}, h('td', {}, fecha(x.fecha)), h('td', { class: 'num' }, `${x.filas_co} de ${x.filas}`), h('td', { class: 'num' }, pct1(x.pct_filas)),
        h('td', { class: 'num' }, variacion(x.var_filas)),
        h('td', { class: 'num' }, x.guias ? `${x.guias_co} de ${x.guias}` : (x.guias_co ?? '—')))))) : null);
}

// ------------------------------------------------------------------ compras en Miami (bloque B3)
function bloqueMiami(c) {
  const caja = h('div', { class: 'bloque' }, h('h3', {}, 'Compra en Miami'));
  const recargar = async (msg) => { aviso(msg); await cargarCasos(); pintarTabla(); abrirDetalle(c.key_notif); };
  intentar(async () => {
    const r = await api('GET', `/api/cases/${encodeURIComponent(c.key_notif)}/miami`);
    const activa = r.compras.find((x) => x.estado === 'Comprada');
    const editable = puedeEditar();
    if (activa) {
      caja.append(h('dl', { class: 'datos' },
        dato('Comprada', `${fechaLarga(activa.fecha_compra)} (registró ${activa.username || '—'})`),
        dato('Parte y cantidad', `${activa.part} × ${activa.cantidad}`),
        dato('PO o referencia', activa.po || '—'),
        dato('Llegada esperada', fechaLarga(activa.eta)),
        activa.notas ? dato('Notas', activa.notas) : null));
      if (editable) {
        const fRec = h('input', { type: 'date', class: 'solo', value: hoyISO() });
        const eta = h('input', { type: 'date', class: 'solo', value: activa.eta || '' });
        const motivo = h('input', { class: 'solo', placeholder: 'Motivo (ej. llegó por el KGB)', maxlength: 300, size: 28 });
        caja.append(
          h('div', { class: 'acciones' }, fRec, h('button', { class: 'btn', type: 'button', onclick: intentar(async () => {
            await api('PATCH', `/api/miami/${activa.id}`, { accion: 'recibida', fecha: fRec.value });
            await recargar('Compra recibida. Se registró la recepción del caso.');
          }) }, 'Marcar recibida')),
          h('div', { class: 'acciones' }, eta, h('button', { class: 'btn sec', type: 'button', onclick: intentar(async () => {
            await api('PATCH', `/api/miami/${activa.id}`, { accion: 'editar', eta: eta.value });
            await recargar('Llegada esperada actualizada.');
          }) }, 'Cambiar llegada')),
          h('div', { class: 'acciones' }, motivo, h('button', { class: 'btn sec', type: 'button', onclick: intentar(async () => {
            if (!motivo.value.trim()) { aviso('Indique el motivo de la cancelación.', true); return; }
            await api('PATCH', `/api/miami/${activa.id}`, { accion: 'cancelada', motivo: motivo.value });
            await recargar('Compra cancelada.');
          }) }, 'Cancelar compra')));
      }
    } else if (editable) {
      const g = {
        fecha_compra: h('input', { type: 'date', value: hoyISO() }), cantidad: h('input', { type: 'number', min: 1, max: 999, value: 1 }),
        part: h('input', { value: c.assembly || '', maxlength: 40 }), po: h('input', { maxlength: 60 }),
        eta: h('input', { type: 'date' }), notas: h('input', { maxlength: 500 }),
      };
      caja.append(h('p', { class: 'nota' }, c.option_jenny === 'Miami' ? 'Jenny indicó Miami y no hay compra registrada.' : 'Sin compra en Miami.',
        ` Si deja la llegada vacía, se calcula como la compra + ${r.lead_dias} días.`),
      h('div', { class: 'form' },
        h('label', { class: 'campo' }, 'Fecha de compra', g.fecha_compra), h('label', { class: 'campo' }, 'Cantidad', g.cantidad),
        h('label', { class: 'campo' }, 'Parte comprada', g.part), h('label', { class: 'campo' }, 'PO o referencia', g.po),
        h('label', { class: 'campo' }, 'Llegada esperada (opcional)', g.eta), h('label', { class: 'campo' }, 'Notas', g.notas)),
      h('div', { class: 'acciones' }, h('button', { class: 'btn', type: 'button', onclick: intentar(async () => {
        const body = { fecha_compra: g.fecha_compra.value, cantidad: Number(g.cantidad.value) || 1, part: g.part.value,
          po: g.po.value, eta: g.eta.value || null, notas: g.notas.value };
        const res = await api('POST', `/api/cases/${encodeURIComponent(c.key_notif)}/miami`, body);
        await recargar(`Compra registrada. Llegada esperada: ${fechaLarga(res.eta)}.`);
      }) }, 'Registrar compra')));
    } else {
      caja.append(h('p', { class: 'nota' }, 'Sin compra en Miami registrada.'));
    }
    const cerradas = r.compras.filter((x) => x.estado !== 'Comprada');
    if (cerradas.length) {
      caja.append(h('p', { class: 'nota' }, 'Anteriores: ', cerradas.map((x) => `${x.estado} el ${fechaLarga(x.fecha_estado)}${x.motivo ? ` (${x.motivo})` : ''}, comprada el ${fechaLarga(x.fecha_compra)}${x.po ? `, PO ${x.po}` : ''}`).join(' · ')));
    }
  })();
  return caja;
}

async function panelMiami() {
  const r = await api('GET', `/api/miami?todas=${S.mTodas ? 1 : 0}`);
  const res = r.resumen;
  const cifra = (valor, label, cls) => h('div', { class: `cifra${cls ? ` ${cls}` : ''}` }, h('b', {}, valor), h('span', {}, label));
  const todas = h('input', { type: 'checkbox', checked: !!S.mTodas, onchange: intentar(async (ev) => { S.mTodas = ev.target.checked; await pintarPartes(); }) });
  return h('div', { class: 'panel' }, h('h2', {}, 'Compras en Miami'),
    h('p', {}, `Se registran en el detalle de cada caso. Si no se indica la llegada, es la compra + ${r.lead_dias} días (Administración). `,
      '"Apple lo trae antes" sale cuando lo que viene en el KGB o en Open Orders alcanza a despacharse dentro del límite de fill rate, o llega antes que Miami.'),
    h('div', { class: 'tira' },
      cifra(res.activas, 'compras activas'),
      cifra(res.evaluar, 'para evaluar cancelar: Apple lo trae antes', res.evaluar ? 'alerta-cifra' : ''),
      cifra(res.atrasadas, 'atrasadas: pasó la llegada esperada', res.atrasadas ? 'alerta-cifra' : ''),
      cifra(res.miami_sin_compra, 'casos Miami sin compra registrada')),
    h('label', { class: 'check' }, todas, 'Incluir recibidas y canceladas'),
    r.compras.length ? h('table', { class: 'mini' },
      h('thead', {}, h('tr', {}, ['Caso', 'Tienda', 'Parte', 'Cant.', 'Comprada', 'PO', 'Llegada esperada', 'Límite', 'Estado', 'Alerta'].map((t) => h('th', {}, t)))),
      h('tbody', {}, r.compras.map((x) => h('tr', {},
        h('td', {}, h('button', { class: 'btn sec', type: 'button', title: 'Abrir el caso', onclick: () => { S.f.q = x.key_notif; S.sel = x.key_notif; mostrar('casos'); } }, x.key_notif)),
        h('td', {}, x.tienda || '—'), h('td', {}, x.part), h('td', { class: 'num' }, x.cantidad),
        h('td', { class: 'fecha' }, fecha(x.fecha_compra)), h('td', {}, x.po || '—'), h('td', { class: 'fecha' }, fecha(x.eta)),
        h('td', { class: 'fecha' }, fecha(x.limite_fr)), h('td', {}, x.estado, x.caso_abierto ? null : h('span', { class: 'sub' }, 'El caso ya salió del reporte'),
          x.motivo ? h('span', { class: 'sub' }, x.motivo) : null),
        h('td', {}, x.alertas.map((a) => h('div', { class: `alerta ${a.level}` }, a.text)))))))
      : h('p', { class: 'nota' }, 'Todavía no hay compras en Miami registradas.'));
}

// ------------------------------------------------------------------ vista de tienda y tiendas por usuario
const ESTADO_TIENDA = {
  'En bodega, por despachar': 'El repuesto ya está en nuestra bodega; la fecha es el despacho estimado.',
  'En proceso': 'El repuesto viene en camino; la fecha es tentativa (ETA de Apple) y puede cambiar.',
  'En gestión': 'Aún no tenemos una fecha confiable; le informaremos apenas la tengamos.',
  Despachado: 'Despachado en los últimos días.',
};

async function pintarTienda(preview, destino) {
  const v = vaciar(destino || $('#v-tienda'));
  const r = await api('GET', `/api/tienda/casos${preview ? `?ship_to=${encodeURIComponent(preview)}` : ''}`);
  const cifra = (valor, label) => h('div', { class: 'cifra' }, h('b', {}, valor), h('span', {}, label));
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Estado de sus casos de repuesto'),
    h('p', {}, r.tiendas.length ? `${r.tiendas.map((t) => t.nombre).join(', ')}. Información según el reporte del ${fecha(r.actualizado)}.`
      : 'Su usuario todavía no tiene tiendas asignadas. Escríbanos para habilitarlo.'),
    h('dl', { class: 'datos' }, Object.entries(ESTADO_TIENDA).map(([k, t]) => dato(k, k === 'Despachado' ? `Despachado en los últimos ${r.dias_despachados} días.` : t)))));
  if (!r.tiendas.length) return;
  v.append(h('div', { class: 'tira' }, Object.keys(ESTADO_TIENDA).map((k) => cifra(r.resumen[k] || 0, k))));
  const varias = r.tiendas.length > 1;
  const buscar = h('input', { type: 'search', placeholder: 'Notificación, repuesto o equipo', oninput: () => filas() });
  const tienda = varias ? h('select', { onchange: () => filas() }, h('option', { value: '' }, 'Todas'),
    r.tiendas.map((t) => h('option', { value: t.ship_to }, t.nombre))) : null;
  const cuerpo = h('tbody'); const res = h('p', { class: 'resumen' });
  v.append(h('div', { class: 'barra' }, h('label', { class: 'campo' }, 'Buscar', buscar), varias ? h('label', { class: 'campo' }, 'Tienda', tienda) : null), res,
    h('div', { class: 'marco' }, h('table', {}, h('thead', {}, h('tr', {},
      ['Notificación', varias ? 'Tienda' : null, 'Repuesto', 'Equipo', 'Creado', 'Estado', 'Fecha'].filter(Boolean).map((t) => h('th', {}, t)))), cuerpo)));
  function filas() {
    const q = buscar.value.trim().toLowerCase(); const st = tienda ? tienda.value : '';
    const lista = r.casos.filter((x) => (!st || x.ship_to === st)
      && (!q || `${x.notificacion} ${x.parte} ${x.descripcion || ''} ${x.equipo || ''}`.toLowerCase().includes(q)));
    vaciar(res).append(h('b', {}, lista.length), ` de ${r.casos.length} casos`);
    vaciar(cuerpo).append(...lista.map((x) => h('tr', {},
      h('td', {}, h('span', { class: 'caso' }, x.notificacion)),
      varias ? h('td', { class: 'tienda' }, x.tienda) : null,
      h('td', {}, h('span', { class: 'parte' }, x.parte), h('span', { class: 'sub desc' }, x.descripcion)),
      h('td', {}, x.equipo || '—'), h('td', { class: 'fecha' }, fecha(x.creado)),
      h('td', {}, h('span', { class: `etiqueta${x.estado === 'Despachado' ? ' despachado' : ''}` }, x.estado)),
      h('td', { class: 'fecha' }, x.fecha ? fecha(x.fecha) : '—', x.fecha_tipo ? h('span', { class: 'sub' }, x.fecha_tipo) : null))));
    if (!lista.length) cuerpo.append(h('tr', {}, h('td', { colspan: 7, class: 'vacio' }, 'Ningún caso coincide.')));
  }
  filas();
}

async function panelTiendasUsuarios(usuarios) {
  const { tiendas } = await api('GET', '/api/tiendas-disponibles');
  const caja = h('div', { class: 'panel' }, h('h2', {}, 'Tiendas de cada usuario'),
    h('p', {}, 'Un usuario con rol "tienda" solo ve los casos de los ship-to que se le asignen aquí. El ship-to es el código del backlog: cuando el backlog pase a Ingram (2 de noviembre) hay que revisar estas asignaciones.'));
  const deTienda = usuarios.filter((u) => u.role === 'tienda');
  if (!deTienda.length) caja.append(h('p', { class: 'nota' }, 'No hay usuarios con rol tienda. Créelos arriba con el rol "tienda".'));
  for (const u of deTienda) {
    const marcado = new Set((await api('GET', `/api/users/${u.id}/tiendas`)).ship_tos);
    const resumen = h('span', { class: 'sub' });
    const pintarResumen = () => {
      resumen.textContent = marcado.size ? tiendas.filter((t) => marcado.has(t.ship_to)).map((t) => t.nombre).join(', ') || `${marcado.size} ship-to` : 'Sin tiendas asignadas';
    };
    const filtro = h('input', { type: 'search', class: 'solo', placeholder: 'Filtrar tiendas' });
    const lista = h('div', { class: 'lista-tiendas' }, tiendas.map((t) => h('label', { class: 'check', dataset: { buscar: `${t.nombre} ${t.ship_to}`.toLowerCase() } },
      h('input', { type: 'checkbox', checked: marcado.has(t.ship_to), onchange: (ev) => { if (ev.target.checked) marcado.add(t.ship_to); else marcado.delete(t.ship_to); } }),
      `${t.nombre} (${t.ship_to})${t.abiertos ? `, ${t.abiertos} abiertos` : ''}`)));
    filtro.addEventListener('input', () => {
      const q = filtro.value.trim().toLowerCase();
      lista.querySelectorAll('label').forEach((l) => { l.hidden = !!q && !l.dataset.buscar.includes(q); });
    });
    const editor = h('div', { class: 'editor-tiendas', hidden: true }, filtro, lista, h('div', { class: 'acciones' }, h('button', {
      class: 'btn', type: 'button', onclick: intentar(async () => {
        await api('PUT', `/api/users/${u.id}/tiendas`, { ship_tos: [...marcado] }); pintarResumen(); editor.hidden = true; aviso('Tiendas guardadas.');
      }),
    }, 'Guardar tiendas')));
    pintarResumen();
    caja.append(h('div', { class: 'usuario-tienda' }, h('b', {}, u.username), resumen,
      h('button', { class: 'btn sec', type: 'button', onclick: () => { editor.hidden = !editor.hidden; } }, 'Editar tiendas'), editor));
  }
  const sel = h('select', { class: 'solo' }, tiendas.map((t) => h('option', { value: t.ship_to }, `${t.nombre} (${t.ship_to})`)));
  const previa = h('div');
  caja.append(h('h2', { class: 'separado' }, 'Vista previa'), h('p', { class: 'nota' }, 'Así ve una tienda sus casos.'),
    h('div', { class: 'barra' }, sel, h('button', { class: 'btn sec', type: 'button', onclick: intentar(async () => { await pintarTienda(sel.value, previa); }) }, 'Ver como esa tienda')),
    previa);
  return caja;
}

// ------------------------------------------------------------------ inventario propio y correos de tiendas
async function panelInventario() {
  const r = await api('GET', '/api/inventario/resumen');
  const caja = h('div', { class: 'panel' }, h('h2', {}, 'Inventario propio'));
  if (!r.fecha) {
    caja.append(h('p', {}, 'Todavía no se ha cargado un archivo de inventario. Cárguelo en Cargas → Inventario propio (hay plantilla). Con él, el tablero reparte las unidades entre los casos por prioridad y avisa qué se puede despachar ya y qué casos OH no tienen inventario.'));
    return caja;
  }
  const cifra = (valor, label, cls) => h('div', { class: `cifra${cls ? ` ${cls}` : ''}` }, h('b', {}, valor), h('span', {}, label));
  const abrir = (k) => h('button', { class: 'btn sec', type: 'button', title: 'Abrir el caso', onclick: () => { S.f.q = k; S.sel = k; mostrar('casos'); } }, k);
  caja.append(...[h('p', {}, `Archivo del ${fecha(r.fecha)}: ${unid(r.unidades)} unidades de ${r.partes} partes. Las unidades se reparten por prioridad (OH, Super ASP y luego la ETA más cercana; si se acaba la parte, se usa el sustituto oficial). "Con inventario desde" cuenta las cargas seguidas en que la parte tuvo unidades.`),
    h('div', { class: 'tira' },
      cifra(r.casos.length, 'casos que se pueden despachar con inventario propio', r.casos.length ? 'alerta-cifra' : ''),
      cifra(r.oh_sin_inventario.length, 'casos OH de Jenny sin unidades en el inventario', r.oh_sin_inventario.length ? 'alerta-cifra' : '')),
    r.casos.length ? h('div', { class: 'marco' }, h('table', { class: 'mini' },
      h('thead', {}, h('tr', {}, ['Caso', 'Tienda', 'Parte', 'Solución', 'ETA Apple', 'Límite', 'Con inventario desde', 'Días hábiles'].map((t) => h('th', {}, t)))),
      h('tbody', {}, r.casos.slice(0, 300).map((x) => h('tr', {}, h('td', {}, abrir(x.key_notif)), h('td', {}, x.tienda),
        h('td', {}, x.parte, x.via === 'sustituto' ? h('span', { class: 'sub' }, 'con el sustituto') : null), h('td', {}, x.opcion),
        h('td', { class: 'fecha' }, fecha(x.eta)), h('td', { class: 'fecha' }, fecha(x.limite)), h('td', { class: 'fecha' }, fecha(x.desde)),
        h('td', { class: `num${x.dias ? ' dias-neg' : ''}` }, x.dias ?? '—')))))) : null,
    r.oh_sin_inventario.length ? [h('h2', { class: 'separado' }, 'OH sin inventario'), h('table', { class: 'mini' },
      h('thead', {}, h('tr', {}, ['Caso', 'Tienda', 'Parte', 'ETA Apple'].map((t) => h('th', {}, t)))),
      h('tbody', {}, r.oh_sin_inventario.map((x) => h('tr', {}, h('td', {}, abrir(x.key_notif)), h('td', {}, x.tienda), h('td', {}, x.parte),
        h('td', { class: 'fecha' }, fecha(x.eta))))))] : null].flat().filter(Boolean));
  return caja;
}

async function panelAliasTiendas() {
  const r = await api('GET', '/api/tiendas-alias');
  const caja = h('div', { class: 'panel' });
  const repintar = async (msg) => { if (msg) aviso(msg); caja.replaceWith(await panelAliasTiendas()); };
  caja.append(h('h2', {}, 'Correos de las tiendas'),
    h('p', {}, 'El backlog trae los ship-to de CEVA y el archivo de tiendas los de Ingram, por eso no cruzan por código. Las tiendas con el mismo nombre se asocian solas (aunque cambie el orden de las palabras); las demás se asocian aquí revisando la sugerencia. Con la asociación, el correo diario va al Service manager con copia al Coordinador.'),
    h('div', { class: 'acciones' }, h('button', { class: 'btn sec', type: 'button', onclick: intentar(async () => {
      const x = await api('POST', '/api/tiendas-alias/auto'); await repintar(`${x.asociadas} tiendas asociadas por nombre idéntico.`);
    }) }, 'Asociar las de nombre idéntico')));
  if (r.pendientes.length) {
    caja.append(h('h2', { class: 'separado' }, `Sin correo (${r.pendientes.length})`), h('table', { class: 'mini' },
      h('thead', {}, h('tr', {}, ['Tienda en el backlog', 'Ship-to', 'Abiertos', 'Tienda del archivo', 'Parecido', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, r.pendientes.map((p) => {
        const sel = h('select', { class: 'solo' }, h('option', { value: '' }, '— Elegir —'),
          r.archivo.map((a) => h('option', { value: a.ship_to, selected: p.sugerencia && p.sugerencia.ship_to === a.ship_to }, `${a.nombre} (${a.ship_to})`)));
        return h('tr', {}, h('td', {}, p.nombre), h('td', {}, p.ship_to), h('td', { class: 'num' }, p.abiertos || 0), h('td', {}, sel),
          h('td', { class: 'num' }, p.sugerencia ? `${Math.round(p.sugerencia.puntaje * 100)} %` : '—'),
          h('td', {}, h('button', { class: 'btn', type: 'button', onclick: intentar(async () => {
            if (!sel.value) { aviso('Elija la tienda del archivo.', true); return; }
            await api('POST', '/api/tiendas-alias', { ship_to: p.ship_to, ship_to_archivo: sel.value }); await repintar('Tienda asociada.');
          }) }, 'Asociar')));
      }))));
  }
  caja.append(h('h2', { class: 'separado' }, `Asociadas (${r.asociadas.length})`), r.asociadas.length ? h('table', { class: 'mini' },
    h('thead', {}, h('tr', {}, ['Tienda en el backlog', 'Tienda del archivo', 'Para', 'Copia', 'Cómo', ''].map((t) => h('th', {}, t)))),
    h('tbody', {}, r.asociadas.map((a) => h('tr', {}, h('td', {}, a.nombre), h('td', {}, a.nombre_archivo || a.ship_to_archivo), h('td', {}, a.email || '—'),
      h('td', {}, a.cc || '—'), h('td', {}, a.origen),
      h('td', {}, h('button', { class: 'btn sec', type: 'button', onclick: intentar(async () => {
        await api('DELETE', `/api/tiendas-alias/${encodeURIComponent(a.ship_to)}`); await repintar('Asociación quitada.');
      }) }, 'Quitar'))))))
    : h('p', { class: 'nota' }, 'Ninguna todavía.'));
  return caja;
}

// ------------------------------------------------------------------ cargas
async function pintarCargas() {
  const v = vaciar($('#v-cargas'));
  const tarjeta = (kind, titulo, texto, conFecha, plantilla) => {
    const archivo = h('input', { type: 'file', accept: '.xlsx,.xlsm,.csv' });
    const f = h('input', { type: 'date', class: 'solo' });
    const res = h('div', { hidden: true });
    const btn = h('button', {
      class: 'btn', type: 'button', onclick: async () => {
        if (!archivo.files.length) { aviso('Seleccione un archivo.', true); return; }
        const fd = new FormData(); fd.append('file', archivo.files[0]); if (conFecha && f.value) fd.append('fecha', f.value);
        btn.disabled = true; res.hidden = true;
        try {
          const r = await api('POST', `/api/upload/${kind}`, fd);
          res.className = 'resultado'; res.textContent = resumenCarga(kind, r.resultado) + (r.resultado.tiendas_asociadas ? ` ${r.resultado.tiendas_asociadas} tiendas del backlog quedaron con correo (mismo nombre en el archivo de tiendas).` : ''); res.hidden = false;
          S.me = await api('GET', '/api/me'); pintarFuentes(); await cargarCasos(); pintarHistorial();
        } catch (e) { res.className = 'resultado error'; res.textContent = e.message; res.hidden = false; }
        btn.disabled = false; archivo.value = '';
      },
    }, 'Cargar');
    return h('div', { class: 'panel carga' }, h('h2', {}, titulo), h('p', {}, texto),
      puedeEditar() ? h('div', { class: 'barra' }, h('label', { class: 'campo' }, 'Archivo', archivo),
        conFecha ? h('label', { class: 'campo' }, 'Fecha (si no está en el nombre)', f) : null, btn) : h('p', { class: 'nota' }, 'Su rol solo permite consultar.'),
      plantilla ? h('a', { href: `/api/templates/${kind}`, class: 'nota' }, 'Descargar plantilla') : null, res);
  };
  v.append(h('div', { class: 'rejilla' },
    tarjeta('backlog', 'Backlog diario', 'El archivo de la KAM (hojas Backlog y Allocated). Actualiza los casos que siguen, crea los nuevos y cierra los que ya no aparecen. Nunca borra la gestión del equipo.', true),
    tarjeta('kgb', 'KGB diario', 'Tránsito de Apple. Se filtra al cliente configurado y reemplaza al KGB anterior. El cruce se hace por número de guía.', true),
    tarjeta('openorders', 'Open Orders diario', 'Reporte Colombia-Open Orders de Apple: lo pedido que todavía no sale. Reemplaza al anterior y guarda la foto del día. Cárguelo en orden de fecha.', true),
    tarjeta('inventario', 'Inventario propio', 'Nuestro inventario en bodega (on hand). Acepta columnas como Parte o Material, y Cantidad, Libre utilización u On hand; la bodega es opcional. Reemplaza al anterior y guarda la foto del día.', true, true),
    tarjeta('precios', 'Precios', 'Archivo de precios en COP (tabla dinámica "Average of Precio" o columnas Precio de inventario e intercambio). Cada caso se valora a stocking.', false, true),
    tarjeta('partes', 'Parts Data (sustitutos)', 'Parts Data de Apple (.xlsx). Toma descripción y sustituto oficial de cada parte; sus precios no se usan. Tarda cerca de un minuto.', false),
    tarjeta('shipto', 'Tiendas', 'Archivo ASPs-Ingram-SHIPTO: shipto, nombre, ciudad, Service manager (para) y Coordinador (copia). No se guardan direcciones.', false, true)));
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Historial de cargas'), h('div', { id: 'historial' })));
  pintarHistorial();
}

function resumenCarga(kind, r) {
  if (kind === 'backlog') return `Backlog del ${fecha(r.fecha)}: ${r.nuevas} nuevas, ${r.actualizadas} siguen, ${r.cerradas} salieron (${r.asignadas} asignadas), ${r.reabiertas} reabiertas, ${r.cambios_eta} cambios de ETA. Abiertas: ${r.abiertas}.`;
  if (kind === 'kgb') return `KGB del ${fecha(r.fecha)}: ${r.filas_cliente} de ${r.filas_archivo} filas del cliente, ${r.guias} guías. Va a Colombia el ${pct1(r.pct_filas_co)} de las líneas${r.pct_filas_co_ant !== undefined && r.pct_filas_co_ant !== null ? ` (${pts1(Math.round((r.pct_filas_co - r.pct_filas_co_ant) * 10) / 10)} frente al ${fecha(r.fecha_ant)})` : ''}.`;
  if (kind === 'inventario') return `Inventario del ${fecha(r.fecha)}: ${r.unidades} unidades de ${r.partes} partes (${r.filas} filas). Columnas usadas: ${Object.values(r.columnas).join(', ')}.`;
  if (kind === 'openorders') return `Open Orders del ${fecha(r.fecha)}: ${r.lineas} líneas de ${r.ordenes} órdenes (${r.unidades} unid.), ${r.nuevas} nuevas, ${r.salieron} salieron, ${r.sin_fecha} sin fecha de salida.${r.columnas_nuevas.length ? ` ATENCIÓN, columnas nuevas en el archivo: ${r.columnas_nuevas.join(', ')}.` : ''}`;
  if (kind === 'precios') return `${r.partes} partes con precio, ${r.con_exchange} con precio exchange.`;
  if (kind === 'partes') return `${r.partes} partes, ${r.con_sustituto} con sustituto oficial.`;
  return `${r.tiendas} tiendas cargadas${r.inactivas_omitidas ? ` (${r.inactivas_omitidas} inactivas omitidas)` : ''}${r.con_copia ? ', con correo y copia' : ''}.`;
}

const intentarHist = intentar(async () => {
  const el = $('#historial'); if (!el) return;
  const rows = await api('GET', '/api/loads');
  vaciar(el).append(rows.length ? h('table', { class: 'mini' },
    h('thead', {}, h('tr', {}, ['Tipo', 'Archivo', 'Fecha del archivo', 'Cargado', 'Por', 'Filas'].map((t) => h('th', {}, t)))),
    h('tbody', {}, rows.map((r) => h('tr', {}, h('td', {}, r.kind), h('td', {}, r.file_name), h('td', {}, fecha(r.file_date)),
      h('td', {}, r.loaded_at.replace('T', ' ')), h('td', {}, r.username || '—'), h('td', { class: 'num' }, r.rows))))) : h('p', { class: 'nota' }, 'Sin cargas todavía.'));
});
function pintarHistorial() { intentarHist(); }

// ------------------------------------------------------------------ administración
async function pintarAdmin() {
  const v = vaciar($('#v-admin'));
  if (S.me.role !== 'admin') return;
  const [usuarios, cfg, bitacora] = await Promise.all([api('GET', '/api/users'), api('GET', '/api/settings'), api('GET', '/api/audit?limit=200')]);

  const nu = { username: h('input', { maxlength: 64 }), full_name: h('input', { maxlength: 120 }),
    role: h('select', {}, S.me.roles.map((r) => h('option', { value: r, selected: r === 'editor' }, r))), password: h('input', { type: 'password', autocomplete: 'new-password' }) };
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Usuarios'),
    h('p', {}, 'Administrador: todo. Editor: gestiona casos, guías, correos y cargas. Lector: solo consulta. Apple Care Manager (acm): ve casos, abastecimiento e indicadores sin precios ni compras en Miami, no edita y deja notas. Tienda: solo ve sus casos. Las contraseñas nuevas o restablecidas obligan a cambiarlas en el siguiente ingreso.'),
    h('table', { class: 'mini' }, h('thead', {}, h('tr', {}, ['Usuario', 'Nombre', 'Rol', 'Activo', 'Estado', ''].map((t) => h('th', {}, t)))),
      h('tbody', {}, usuarios.map((u) => {
        const rol = h('select', { class: 'solo', onchange: intentar(async (ev) => { await api('PATCH', `/api/users/${u.id}`, { role: ev.target.value }); aviso('Rol actualizado.'); }) },
          S.me.roles.map((r) => h('option', { value: r, selected: r === u.role }, r)));
        const activo = h('input', { type: 'checkbox', checked: !!u.active, onchange: intentar(async (ev) => { await api('PATCH', `/api/users/${u.id}`, { active: ev.target.checked }); aviso('Usuario actualizado.'); }) });
        const bloqueado = u.locked_until && u.locked_until > new Date().toISOString().slice(0, 19);
        return h('tr', {}, h('td', {}, u.username), h('td', {}, u.full_name), h('td', {}, rol), h('td', {}, activo),
          h('td', {}, bloqueado ? 'Bloqueado' : u.must_change_pw ? 'Debe cambiar clave' : 'Normal'),
          h('td', {}, h('button', { class: 'btn sec', type: 'button', onclick: intentar(async () => {
            const p = prompt(`Nueva contraseña temporal para ${u.username} (mín. 12 caracteres, letras y números):`);
            if (!p) return;
            await api('PATCH', `/api/users/${u.id}`, { new_password: p, unlock: true }); aviso('Contraseña restablecida.');
          }) }, 'Restablecer clave')));
      }))),
    h('h2', { class: 'separado' }, 'Nuevo usuario'),
    h('div', { class: 'barra' }, h('label', { class: 'campo' }, 'Usuario', nu.username), h('label', { class: 'campo' }, 'Nombre', nu.full_name),
      h('label', { class: 'campo' }, 'Rol', nu.role), h('label', { class: 'campo' }, 'Contraseña temporal', nu.password),
      h('button', { class: 'btn', type: 'button', onclick: intentar(async () => {
        await api('POST', '/api/users', { username: nu.username.value, full_name: nu.full_name.value, role: nu.role.value, password: nu.password.value });
        aviso('Usuario creado.'); pintarAdmin();
      }) }, 'Crear usuario'))));
  v.append(await panelTiendasUsuarios(usuarios));
  v.append(await panelAliasTiendas());

  const etiquetas = {
    kgb_cust_ids: 'Cliente KGB (código; varios separados por coma)', fr_tolerance_days: 'Tolerancia de fill rate (días después de la ETA)',
    fr_tolerance_business: 'Tolerancia en días hábiles (1) o calendario (0)', exchange_codes: 'Garantías con precio exchange (separadas por coma)',
    despachado_grace_days: 'Días hábiles tras la ETA para avisar si un despachado sigue en el reporte', kam_alert_days: 'Días de espera del Apple Care Manager antes de alertar',
    oh_max_days: 'Días hábiles máximos de un OH sin despachar',
    oo_transit_days: 'Open Orders: días de Apple a Colombia después de la salida (Recovery ETA)',
    miami_lead_days: 'Miami: días desde la compra hasta la llegada (por defecto)',
    email_transit_days: 'Correo: días hábiles de tránsito confirmado que también llevan fecha (0 = solo repuestos en bodega)', currency: 'Moneda de los precios', email_signature: 'Firma de los correos',
  };
  const inputs = {};
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Parámetros'),
    h('div', { class: 'form' }, Object.entries(etiquetas).map(([k, label]) => {
      inputs[k] = k === 'email_signature' ? h('textarea', { value: cfg.settings[k] || '' }) : h('input', { value: cfg.settings[k] || '' });
      if (k === 'email_signature') inputs[k].value = cfg.settings[k] || '';
      return h('label', { class: `campo${k === 'email_signature' ? ' ancho' : ''}` }, label, inputs[k]);
    })),
    h('div', { class: 'acciones' }, h('button', { class: 'btn', type: 'button', onclick: intentar(async () => {
      const settings = {}; for (const [k, el] of Object.entries(inputs)) settings[k] = el.value;
      await api('PUT', '/api/settings', { settings }); S.me = await api('GET', '/api/me'); pintarFuentes(); await cargarCasos(); aviso('Parámetros guardados.');
    }) }, 'Guardar parámetros'))));

  const dia = h('input', { type: 'date', class: 'solo' }); const nombre = h('input', { class: 'solo', placeholder: 'Motivo (ej. inventario)' });
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Días no hábiles'),
    h('p', {}, `Los festivos de Colombia se calculan solos. Próximos: ${cfg.festivos.slice(0, 8).map(fecha).join(', ')}. Agregue aquí cierres propios de la bodega.`),
    h('div', { class: 'barra' }, dia, nombre, h('button', { class: 'btn sec', type: 'button', onclick: intentar(async () => {
      await api('POST', '/api/holidays', { day: dia.value, name: nombre.value }); aviso('Día agregado.'); pintarAdmin();
    }) }, 'Agregar día')),
    cfg.festivos_extra.map((x) => h('div', { class: 'acciones' }, `${fechaLarga(x.day)} ${x.name || ''}`,
      h('button', { class: 'btn sec', type: 'button', onclick: intentar(async () => { await api('DELETE', `/api/holidays/${x.day}`); pintarAdmin(); }) }, 'Quitar')))));

  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Bitácora'),
    h('p', {}, 'Registro de solo lectura: ingresos, cargas, cambios de casos, correos y administración. No se puede editar ni borrar desde la app.'),
    h('div', { class: 'marco' }, h('table', { class: 'mini' },
      h('thead', {}, h('tr', {}, ['Cuándo', 'Quién', 'Acción', 'Sobre', 'Detalle'].map((t) => h('th', {}, t)))),
      h('tbody', {}, bitacora.map((b) => h('tr', {}, h('td', { class: 'fecha' }, b.at.replace('T', ' ')), h('td', {}, b.username || '—'),
        h('td', {}, b.action), h('td', {}, b.target || ''), h('td', {}, (b.detail || '').slice(0, 220)))))))));
}

// ------------------------------------------------------------------ cambio de clave
function cambiarClave(obligatorio) {
  const actual = h('input', { type: 'password', autocomplete: 'current-password', required: true });
  const nueva = h('input', { type: 'password', autocomplete: 'new-password', required: true });
  const repite = h('input', { type: 'password', autocomplete: 'new-password', required: true });
  const err = h('div', { class: 'nota', role: 'alert' });
  const modal = h('div', { class: 'modal' }, h('form', {
    onsubmit: async (ev) => {
      ev.preventDefault(); err.textContent = '';
      if (nueva.value !== repite.value) { err.textContent = 'Las contraseñas no coinciden.'; return; }
      try {
        await api('POST', '/api/password', { current: actual.value, new: nueva.value });
        location.reload();
      } catch (e) { err.textContent = e.message; }
    },
  }, h('h2', {}, 'Cambie su contraseña'),
  h('p', { class: 'nota' }, obligatorio ? 'Su contraseña es temporal. Defina una propia de al menos 12 caracteres, con letras y números.' : ''),
  h('label', { class: 'campo' }, 'Contraseña actual', actual), h('label', { class: 'campo' }, 'Nueva contraseña', nueva),
  h('label', { class: 'campo' }, 'Repita la nueva contraseña', repite), err,
  h('button', { class: 'btn', type: 'submit' }, 'Guardar contraseña')));
  document.body.append(modal); actual.focus();
}

iniciar().catch((e) => aviso(e.message, true));
