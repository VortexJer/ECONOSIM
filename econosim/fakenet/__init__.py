"""Internet falso: lo único que ve la IA.

* `server.FakeNet`: proxy HTTP/HTTPS que enruta por Host a los gemelos y deja
  colgada (sin respuesta, como un firewall que descarta) cualquier otra petición.
* `dns.FakeDNS`: resuelve CUALQUIER nombre a la IP del proxy. Así "internet"
  existe, pero todo lo que no es un gemelo agota el timeout.
* `certs`: CA propia (la IA la tiene instalada como confiable, igual que en
  una red corporativa) y certificado con todos los dominios gemelos.
"""
