'use strict';
/* Backlog ASP. Todo el texto de los datos se inserta con textContent (nunca innerHTML). */

const S = {
  me: null, casos: [], hoy: null, kgbFecha: null, cargado: {},
  f: { q: '', opcion: '', tienda: '', estado: '', alerta: '', guia: '', superAsp: false, cerrados: false },
  grupo: '', orden: 'prioridad', colapsados: new Set(), sel: null,
};
const puedeEditar = () => S.me && S.me.role !== 'lector';

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
  $('#quien').textContent = `${S.me.full_name || S.me.username} (${S.me.role})`;
  $('#tab-admin').hidden = S.me.role !== 'admin';
  $('#salir').addEventListener('click', intentar(async () => { await api('POST', '/api/logout'); location.href = '/login'; }));
  document.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => mostrar(b.dataset.tab)));
  if (S.me.must_change_pw) return cambiarClave(true);
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
    item('Backlog', u.backlog), item('KGB', u.kgb), item('Precios', u.precios), item('Tiendas', u.shipto),
    h('span', {}, `Fill rate: despacho hasta ETA + ${S.me.tolerancia} días`),
  );
}

const vistas = { casos: pintarCasos, guias: pintarGuias, correos: pintarCorreos, indicadores: pintarIndicadores, cargas: pintarCargas, admin: pintarAdmin };
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
  GUIA_SIN_PARTE: 'Guía sin la parte', ETA_ANTES_KGB: 'ETA antes de la llegada KGB', GUIA_RETRASADA: 'Guía retrasada',
  SIN_RECEPCION: 'Llegada sin recepción', POSIBLE_DOBLE_COMPRA: 'Miami con parte en KGB', KAM_PENDIENTE: 'Esperando a la KAM',
  DESPACHADO_SIGUE: 'Despachado y sigue en reporte', REABIERTO: 'Reabierto', SIN_GUIA: 'In Transit sin guía',
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
      [['prioridad', 'Prioridad'], ['limite', 'Días al límite de fill rate'], ['eta', 'Días a la ETA'], ['edad', 'Edad (más antiguos)'], ['tienda', 'Tienda']]
        .map(([k, t]) => h('option', { value: k, selected: S.orden === k }, t)))),
    h('span', { class: 'separa' }),
    h('a', { class: 'btn sec', href: '/api/export/cases.csv' }, 'Exportar CSV'),
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
  h('td', {}, h('span', { class: `etiqueta${c.option_short === 'OH' ? ' oh' : ''}` }, c.option_short)),
  h('td', { class: 'fecha' }, fecha(c.eta_jenny),
    h('span', { class: `sub${dias !== null && dias < 0 ? ' dias-neg' : ''}` }, dias === null ? '' : dias === 0 ? 'hoy' : dias > 0 ? `en ${dias} d` : `hace ${-dias} d`)),
  h('td', {}, carril(c)),
  h('td', { class: 'fecha' }, fecha(c.despacho_posible), h('span', { class: 'sub' }, c.fecha_despacho ? 'despachado' : c.llegada_fuente)),
  h('td', { class: 'num c-edad' }, c.edad ?? '—'),
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
    ' | ', h('b', {}, new Set(lista.map((c) => c.notifctn)).size), ' reparaciones', ' | valor pendiente ', h('b', {}, dinero(valor, lista[0]?.moneda)),
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
  kam_solicitado: 'KAM solicitada', kam_respuesta: 'KAM respondida',
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
      campo('Aprobación de la KAM', g.aprobacion_kam),
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

  panel.append(h('div', { class: 'bloque' }, h('h3', {}, 'Datos de Apple (solo lectura)'), h('dl', { class: 'datos' },
    dato('Solución', c.option_jenny || '—'),
    dato('Comentarios', c.comments_jenny || '—'),
    dato('Notas', c.notes_jenny || '—'),
    c.subs_list.length ? dato('Sustitutos', c.subs_list.join(', ')) : null,
    dato('Parte', `${c.assembly} ${c.description || ''}`),
    dato('Equipo', c.product_description || c.material_desc || '—'),
    dato('Garantía', `${c.warranty || '—'} (precio ${c.tipo_precio})`),
    dato('Estrategia', `${c.repair_strategy || '—'}${c.super_asp ? ', se repara en el Super ASP' : ''}`),
    dato('PO de la tienda', c.po_number || '—'),
    dato('Serial', c.serial_no || '—'),
    dato('Creado', fechaLarga(c.created_on)),
    dato('En el reporte desde', fechaLarga(c.first_seen)),
  )));

  const texto = h('textarea', { maxlength: 2000, placeholder: 'Cómo se está solucionando este caso…' });
  panel.append(h('div', { class: 'bloque' }, h('h3', {}, 'Comentarios'),
    editable ? [texto, h('div', { class: 'acciones' }, h('button', {
      class: 'btn sec', type: 'button', onclick: intentar(async () => {
        if (!texto.value.trim()) return;
        await api('POST', `/api/cases/${encodeURIComponent(key)}/comments`, { text: texto.value });
        aviso('Comentario agregado.'); abrirDetalle(key);
      }),
    }, 'Agregar comentario'))] : null,
    det.comentarios.length ? det.comentarios.map((m) => h('div', { class: 'comentario' }, h('small', {}, `${m.username}, ${m.at.replace('T', ' ')}`), m.text))
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
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Guías de Apple'),
    h('p', {}, 'Cada guía agrupa los casos que viajan en el mismo envío. Cuando llegue, registre la llegada una sola vez y se marcan todos sus casos como recibidos. ',
      `El cruce usa el número de guía de los comentarios de la KAM contra el KGB del ${r.kgb_fecha ? fecha(r.kgb_fecha) : '(sin cargar)'}.`)));
  if (!r.guias.length) { v.append(h('div', { class: 'vacio' }, 'No hay guías en los casos abiertos.')); return; }
  const tabla = h('table', {},
    h('thead', {}, h('tr', {}, ['Guía', 'Casos pendientes', 'Partes', 'Tiendas', 'En KGB', 'Llegada KGB', 'Evento KGB', 'ETA Apple más cercana', 'Retraso', 'Llegada registrada', ''].map((t) => h('th', {}, t)))),
    h('tbody', {}, r.guias.map((g) => {
      const f = h('input', { type: 'date', class: 'solo', value: hoyISO() });
      return h('tr', {},
        h('td', {}, h('button', { class: 'btn sec', type: 'button', title: 'Ver sus casos', onclick: () => { S.f.guia = g.guia; S.sel = null; mostrar('casos'); } }, g.guia)),
        h('td', { class: 'num' }, `${g.pendientes} / ${g.casos}`, g.rojas ? h('span', { class: 'sub dias-neg' }, `${g.rojas} en rojo`) : null),
        h('td', { class: 'num' }, g.partes), h('td', { class: 'num' }, g.tiendas),
        h('td', {}, g.en_kgb ? h('span', { class: 'badge-si' }, `Sí (${g.kgb_lineas})`) : h('span', { class: 'badge-no' }, 'No')),
        h('td', { class: 'fecha' }, fecha(g.kgb_edd)), h('td', {}, g.kgb_evento || '—'),
        h('td', { class: 'fecha' }, fecha(g.eta_min)),
        h('td', {}, g.retrasada ? h('span', { class: 'badge-no' }, 'Retrasada') : '—'),
        h('td', { class: 'fecha' }, g.llegada ? fecha(g.llegada) : '—'),
        h('td', {}, puedeEditar() && g.pendientes ? h('div', { class: 'acciones' }, f, h('button', {
          class: 'btn', type: 'button', onclick: intentar(async () => {
            const res = await api('POST', `/api/guides/${encodeURIComponent(g.guia)}/arrival`, { fecha: f.value });
            aviso(`Llegada registrada: ${res.actualizados} de ${res.casos} casos actualizados.`);
            await cargarCasos(); pintarGuias();
          }),
        }, 'Registrar llegada')) : null));
    })));
  v.append(h('div', { class: 'marco' }, tabla));
}

// ------------------------------------------------------------------ correos
async function pintarCorreos() {
  const v = vaciar($('#v-correos'));
  const dia = h('input', { type: 'date', value: S.correoFecha || hoyISO() });
  const modo = h('select', {}, h('option', { value: 'gestionados', selected: S.correoModo !== 'todos' }, 'Casos gestionados ese día'),
    h('option', { value: 'todos', selected: S.correoModo === 'todos' }, 'Todos los casos pendientes'));
  const lista = h('div');
  const refrescar = intentar(async () => {
    S.correoFecha = dia.value; S.correoModo = modo.value;
    const r = await api('GET', `/api/emails?fecha=${dia.value}&modo=${modo.value}`);
    vaciar(lista);
    if (!r.grupos.length) { lista.append(h('div', { class: 'vacio' }, 'No hay casos gestionados ese día. Cambie a "Todos los casos pendientes" para armar un correo completo.')); return; }
    const nCasos = r.grupos.reduce((s, g) => s + g.casos.length, 0);
    lista.append(h('p', { class: 'resumen' }, h('b', {}, r.grupos.length), r.grupos.length === 1 ? ' tienda, ' : ' tiendas, ', h('b', {}, nCasos), nCasos === 1 ? ' caso.' : ' casos.'));
    for (const g of r.grupos) lista.append(tarjetaCorreo(g, r));
  });
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Correos por tienda'),
    h('p', {}, 'Un correo por tienda, solo con tiempos: notificación, PO de la tienda y fecha estimada de despacho. Copie el texto o descargue el borrador (.eml se abre en Outlook listo para enviar). Al hacerlo, la app guarda la fecha comunicada.'),
    h('div', { class: 'barra' }, h('label', { class: 'campo' }, 'Día', dia), h('label', { class: 'campo' }, 'Incluir', modo),
      h('button', { class: 'btn', type: 'button', onclick: refrescar }, 'Armar correos'))));
  v.append(lista);
  refrescar();
}

function tarjetaCorreo(g, r) {
  const correo = h('input', { class: 'solo', type: 'email', value: g.email || '', placeholder: 'correo@tienda.com', size: 30 });
  const registrar = async (formato) => api('POST', `/api/emails/${encodeURIComponent(g.ship_to)}`, { fecha: r.fecha, modo: r.modo, formato }, formato === 'eml');
  return h('article', { class: 'correo' },
    h('header', {}, h('h3', {}, g.tienda), h('span', { class: 'sub' }, `Ship-to ${g.ship_to}, ${g.casos.length} ${g.casos.length === 1 ? 'caso' : 'casos'}`),
      h('span', { class: 'separa' }),
      g.email ? null : h('span', { class: 'sin-correo' }, 'Sin correo registrado'),
      puedeEditar() ? [correo, h('button', {
        class: 'btn sec', type: 'button', onclick: intentar(async () => {
          await api('PUT', `/api/shipto/${encodeURIComponent(g.ship_to)}`, { email: correo.value }); aviso('Correo de la tienda guardado.'); pintarCorreos();
        }),
      }, 'Guardar correo')] : null),
    h('pre', {}, `Asunto: ${g.subject}\n\n${g.body}`),
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
    cifra(dinero(k.valor.total, k.moneda), k.valor.sin_precio ? `valor pendiente (${k.valor.sin_precio} líneas sin precio)` : 'valor pendiente'),
    cifra(k.oh.total, `OH por despachar (${k.oh.demorados} demorados)`, k.oh.demorados ? 'alerta-cifra' : ''),
    cifra(k.kam.pendientes, k.kam.dias_prom === null ? 'esperando a la KAM' : `esperando a la KAM (${k.kam.dias_prom} días prom.)`),
  ));
  v.append(h('p', { class: 'nota' }, `Fill rate: líneas cuyo límite (ETA + tolerancia) venció desde el ${fecha(fr.desde)}; a tiempo si la fecha de despacho registrada es igual o anterior al límite.`,
    fr.sin_registro ? ` ${fr.sin_registro} líneas salieron del reporte sin fecha de despacho y no se cuentan.` : ''));

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
        h('thead', {}, h('tr', {}, ['Archivo', 'Nuevas', 'Salieron', 'Neto', 'Abiertas'].map((t) => h('th', {}, t)))),
        h('tbody', {}, flujo.map((f) => h('tr', {}, h('td', {}, fecha(f.fecha)), h('td', { class: 'num' }, f.nuevas),
          h('td', { class: 'num' }, f.salieron), h('td', { class: `num${f.neto > 0 ? ' dias-neg' : ''}` }, f.neto > 0 ? `+${f.neto}` : f.neto),
          h('td', { class: 'num' }, f.abiertas))))) : h('p', { class: 'nota' }, 'Se llena a partir de la segunda carga.'),
      flujo.length > 1 ? barras(flujo.slice(-7).map((f) => [`${fecha(f.fecha)} salieron`, f.salieron]), maxFlujo) : null),
    h('div', { class: 'panel' }, h('h2', {}, 'Edad de lo pendiente'),
      h('p', {}, 'Días desde la creación del caso.'),
      barras(edad.map(([b, n]) => [`${b} días`, n, b === '31+']), Math.max(...edad.map(([, n]) => n)))),
    h('div', { class: 'panel' }, h('h2', {}, 'Por solución'),
      h('p', {}, 'Abiertas por tipo de solución y días promedio de la creación al despacho.'),
      h('table', { class: 'mini' }, h('thead', {}, h('tr', {}, ['Solución', 'Abiertas', 'Despachadas', 'Ciclo prom.'].map((t) => h('th', {}, t)))),
        h('tbody', {}, k.por_opcion.map((o) => h('tr', {}, h('td', {}, o.opcion), h('td', { class: 'num' }, o.abiertas),
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
          res.className = 'resultado'; res.textContent = resumenCarga(kind, r.resultado); res.hidden = false;
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
    tarjeta('precios', 'Precios', 'Lista con columnas de parte, stocking y exchange. Se usa stocking o exchange según la garantía del caso.', false, true),
    tarjeta('shipto', 'Tiendas', 'Ship-to con nombre, correo y ciudad. Si el archivo no trae correo, se conservan los que ya estén registrados.', false, true)));
  v.append(h('div', { class: 'panel' }, h('h2', {}, 'Historial de cargas'), h('div', { id: 'historial' })));
  pintarHistorial();
}

function resumenCarga(kind, r) {
  if (kind === 'backlog') return `Backlog del ${fecha(r.fecha)}: ${r.nuevas} nuevas, ${r.actualizadas} siguen, ${r.cerradas} salieron (${r.asignadas} asignadas), ${r.reabiertas} reabiertas, ${r.cambios_eta} cambios de ETA. Abiertas: ${r.abiertas}.`;
  if (kind === 'kgb') return `KGB del ${fecha(r.fecha)}: ${r.filas_cliente} de ${r.filas_archivo} filas del cliente, ${r.guias} guías.`;
  if (kind === 'precios') return `${r.partes} partes con precio (columnas: ${r.columna_parte}, ${r.stocking || '—'}, ${r.exchange || '—'}).`;
  return `${r.tiendas} tiendas cargadas${r.con_correo ? ' con correo' : ' (el archivo no trae correos)'}.`;
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
    h('p', {}, 'Administrador: todo. Editor: gestiona casos, guías, correos y cargas. Lector: solo consulta. Las contraseñas nuevas o restablecidas obligan a cambiarlas en el siguiente ingreso.'),
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

  const etiquetas = {
    kgb_cust_ids: 'Cliente KGB (código; varios separados por coma)', fr_tolerance_days: 'Tolerancia de fill rate (días después de la ETA)',
    fr_tolerance_business: 'Tolerancia en días hábiles (1) o calendario (0)', exchange_codes: 'Garantías con precio exchange (separadas por coma)',
    despachado_grace_days: 'Días hábiles tras la ETA para avisar si un despachado sigue en el reporte', kam_alert_days: 'Días de espera de la KAM antes de alertar',
    oh_max_days: 'Días hábiles máximos de un OH sin despachar', currency: 'Moneda de los precios', email_signature: 'Firma de los correos',
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
