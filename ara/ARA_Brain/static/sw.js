// Service Worker de ARA (28/08) — mínimo e intencionalmente conservador.
//
// Reglas duras, a propósito:
//  1. NUNCA cachear navegaciones (documentos HTML) ni /api/* — index.html
//     cambia seguido en este proyecto y ya tiene Cache-Control: no-cache,
//     no-store en el servidor (ver ara_server.py, ruta '/'); todo lo que
//     viene de /api/* es tiempo real (stock, rutas, chat) y cachearlo
//     mostraría datos viejos como si fueran actuales — inaceptable en un
//     sistema de inventario/despacho en vivo.
//  2. Solo cachea archivos ESTÁTICOS de verdad (JS/CSS/íconos bajo
//     /static/) con estrategia "stale-while-revalidate": sirve la copia en
//     caché al toque (recarga rápida con señal débil) y de paso pide la
//     versión real en la red para la próxima vez — nunca se queda pegado
//     en una versión vieja para siempre.
//  3. Necesario además para que Chrome ofrezca instalar la app de forma
//     confiable (criterio de instalabilidad) y para poder recibir
//     notificaciones push aunque la pestaña esté cerrada.

const CACHE_ESTATICOS = 'ara-estaticos-v1';

self.addEventListener('install', (evento) => {
  self.skipWaiting();
});

self.addEventListener('activate', (evento) => {
  evento.waitUntil(
    caches.keys().then((nombres) =>
      Promise.all(
        nombres
          .filter((n) => n !== CACHE_ESTATICOS)
          .map((n) => caches.delete(n))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (evento) => {
  const req = evento.request;
  if (req.method !== 'GET') return; // nunca intercepta POST/PUT/DELETE

  const url = new URL(req.url);

  // Regla dura #1: navegaciones y /api/* siempre van directo a la red.
  if (req.mode === 'navigate' || url.pathname.startsWith('/api/')) {
    evento.respondWith(fetch(req));
    return;
  }

  // Regla dura #2: solo /static/ entra al cache stale-while-revalidate.
  if (url.pathname.startsWith('/static/')) {
    evento.respondWith(
      caches.open(CACHE_ESTATICOS).then((cache) =>
        cache.match(req).then((cacheada) => {
          const redFetch = fetch(req)
            .then((resp) => {
              if (resp && resp.status === 200) cache.put(req, resp.clone());
              return resp;
            })
            .catch(() => cacheada); // sin red: si hay copia cacheada, esa sirve
          return cacheada || redFetch;
        })
      )
    );
  }
  // Cualquier otra ruta (no /static/, no /api/, no navegación): sin
  // intervención, se comporta como si no hubiera Service Worker.
});

// ── Notificaciones push (28/08) ─────────────────────────────────────────
// El payload lo arma el backend (ver enviar_push_usuario en push_notif.py)
// como JSON: {"titulo": "...", "cuerpo": "...", "url": "/ruta/a/abrir"}.
self.addEventListener('push', (evento) => {
  let datos = { titulo: 'ARA', cuerpo: 'Tenés una notificación nueva.', url: '/' };
  try {
    if (evento.data) datos = { ...datos, ...evento.data.json() };
  } catch (e) { /* payload no-JSON: se usan los valores por defecto */ }

  evento.waitUntil(
    self.registration.showNotification(datos.titulo, {
      body: datos.cuerpo,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      data: { url: datos.url || '/' },
    })
  );
});

self.addEventListener('notificationclick', (evento) => {
  evento.notification.close();
  const destino = (evento.notification.data && evento.notification.data.url) || '/';
  evento.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((lista) => {
      for (const cliente of lista) {
        if (cliente.url.includes(destino) && 'focus' in cliente) return cliente.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(destino);
    })
  );
});
