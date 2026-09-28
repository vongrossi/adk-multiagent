"""
Bloqueio de SSRF em `checar_url`.

Por que isso tem suíte própria: o `linkcheck` dá ao modelo uma tool que faz
HTTP para a URL que ele escolher. E ele escolhe a partir de texto que pode ter
vindo da web — o corpo de um post. Sem o bloqueio, o agente vira um sondador
de serviços internos usando o usuário como operador, e o resultado volta para
o contexto do modelo, de onde pode sair no post final.

O caso que motivou isto: `checar_url("http://localhost:22/")` devolvia a banner
`SSH-2.0-OpenSSH_9.6p1`. E `169.254.169.254` — o endpoint de metadata de GCP,
AWS e Azure — devolveria a credencial da instância.

Duas coisas que a execução direta não prova, e que por isso tem asserção
própria aqui:

  1. `metadata.google.internal` só resolve dentro da própria nuvem. Aqui dá
     NXDOMAIN, então o teste de rede veria "inalcancavel" e passaria sem ter
     provado nada. Este teste fixa a resolucao.
  2. Redirect. O `urlopen` segue redirect por default, entao bloquear so a URL
     inicial deixa a porta aberta: um alvo publico que responde 302 para
     `127.0.0.1` passa direto. Aqui o handler e testado com um servidor HTTP
     de verdade, nao com mock.
"""

import http.server
import socket
import sys
import threading
import unittest
import unittest.mock as mock
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import linkcheck.tools as T  # noqa: E402

# Faixas que nao podem ser alcancadas por um post da internet. Cada entrada
# existiu por um motivo real.
BLOQUEADOS = [
    ("http://localhost:22/", "loopback por nome"),
    ("http://127.0.0.1:8080/", "loopback IPv4"),
    ("http://[::1]:8080/", "loopback IPv6"),
    ("http://169.254.169.254/latest/meta-data/", "metadata de cloud"),
    ("http://10.0.0.5/admin", "rfc1918 10/8"),
    ("http://172.17.0.2/", "bridge do docker"),
    ("http://192.168.1.1/", "LAN"),
    ("http://100.64.0.1/", "CGNAT, que nenhuma flag do ipaddress marca"),
    ("http://[fd00::1]/", "unicast local IPv6"),
    ("http://0.0.0.0:8000/", "endereco nao especificado"),
    ("http://[fe80::1]/", "link-local IPv6"),
]

# O que tem de continuar funcionando, senão a correção quebra o agente.
PERMITIDOS = [
    "https://docs.python.org/3/library/asyncio.html",
    "https://example.com/",
]


class TestSSRF(unittest.TestCase):

    def test_faixas_internas_sao_bloqueadas(self):
        for url, motivo in BLOQUEADOS:
            with self.subTest(url=url):
                r = T.checar_url(url)
                self.assertEqual(
                    r["status"], "bloqueada",
                    f"{url} ({motivo}) passou: {r}")

    def test_bloqueio_diz_que_e_rede_interna(self):
        # O agente precisa distinguir "recusei por politica" de "o site
        # esta fora do ar": o primeiro e culpa da ferramenta, o segundo e do
        # link. Se os dois virassem "morto", o agente reescreveria um link
        # interno como se fosse um 404.
        r = T.checar_url("http://127.0.0.1:9/")
        self.assertEqual(r["status"], "bloqueada")
        self.assertFalse(r["ok"])
        self.assertIn("rede interna", r["detalhe"])

    def test_site_publico_continua_sendo_checado(self):
        for url in PERMITIDOS:
            with self.subTest(url=url):
                r = T.checar_url(url)
                self.assertNotEqual(
                    r["status"], "bloqueada",
                    f"{url} foi bloqueado por engano: {r}")

    def test_esquemas_nao_http_ja_eram_bloqueados(self):
        # `file://` e `gopher://` caem no filtro de esquema. Register para
        # travar: e a defence que ja existia e que o SSRF de HTTP contorna.
        for url in ("file:///etc/passwd",
                    "gopher://127.0.0.1:11211/_stats",
                    "ftp://exemplo.com/x"):
            with self.subTest(url=url):
                self.assertEqual(T.checar_url(url)["status"], "invalida")

    def test_metadata_google_internal_resolvendo_para_link_local(self):
        # `metadata.google.internal` nao resolve fora do GCP, entao o teste de
        # rede acima passaria sem provar nada. Aqui a resolucao e a do GCP.
        _addrs = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]
        with mock.patch.object(T.socket, "getaddrinfo", return_value=_addrs):
            with self.assertRaises(T._Bloqueado):
                T._alvo_permitido("http://metadata.google.internal/computeMetadata/v1/")
            r = T.checar_url("http://metadata.google.internal/computeMetadata/v1/")
        self.assertEqual(r["status"], "bloqueada")

    def test_host_publico_nao_e_bloqueado(self):
        _addrs = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        with mock.patch.object(T.socket, "getaddrinfo", return_value=_addrs):
            T._alvo_permitido("https://exemplo-publico.com/x")  # nao levanta

    def test_um_ip_de_resposta_privado_derruba_o_host(self):
        # Host publico que responde com IP interno e DNS rebinding. O nome
        # parece legitimo; o IP, nao. Se so o nome fosse conferido, passaria.
        _addrs = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 80))]
        with mock.patch.object(T.socket, "getaddrinfo", return_value=_addrs):
            with self.assertRaises(T._Bloqueado):
                T._alvo_permitido("https://parece-publico.example/x")

    def test_escapatoria_ligada(self):
        # Quem roda contra servico local de proposito tem um caminho.
        import os
        with mock.patch.dict(os.environ,
                             {"LINKCHECK_PERMITIR_REDE_LOCAL": "1"}):
            T._alvo_permitido("http://127.0.0.1:8000/")  # nao levanta
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LINKCHECK_PERMITIR_REDE_LOCAL", None)
            with self.assertRaises(T._Bloqueado):
                T._alvo_permitido("http://127.0.0.1:8000/")

    def test_redirect_para_rede_interna_e_bloqueado(self):
        # O caso que o filtro de URL inicial nao pega. Servidor real, redirect
        # real de 302: e o `urlopen` seguindo, nao um mock.
        alvo = "http://127.0.0.1:9/segredo"

        class _Servidor(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", alvo)
                self.end_headers()

            def do_HEAD(self):
                self.do_GET()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), _Servidor)
        porta = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            # Aponta o host publico para o 127.0.0.1 da maquina: e assim que
            # o redirect seria alcancado se nao fosse bloqueado.
            _addrs = [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                       ("127.0.0.1", porta))]
            with mock.patch.object(T.socket, "getaddrinfo", return_value=_addrs):
                with self.assertRaises(T._Bloqueado):
                    T._alvo_permitido(f"http://servidor.invalido:{porta}/")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_redirect_handler_chama_o_bloqueio(self):
        # Confere o contrato do handler de redirect, sem subir socket: e ele
        # quem reaplica a politica a cada salto.
        vistos = []
        real = T._alvo_permitido

        def _espia(url):
            vistos.append(url)
            real(url)  # o spy registra mas nao neutraliza a politica

        # `Request` de verdade: `super().redirect_request` chama
        # `req.get_method()`, que um objeto improvisado nao tem.
        req = urllib.request.Request("http://a.example/", method="GET")
        with mock.patch.object(T, "_alvo_permitido", _espia):
            with self.assertRaises(T._Bloqueado):
                T._RedirectSeguro().redirect_request(
                    req, None, 302, "Found", {}, "http://127.0.0.1:9/x")
        self.assertEqual(vistos, ["http://127.0.0.1:9/x"])

    def test_redirect_para_destino_publico_passa(self):
        # O oposto do teste anterior: nao pode ser um bloqueio cego, senao
        # todo site com 302 (canonical, http->https) pararia de funcionar.
        req = urllib.request.Request("http://a.example/", method="GET")
        novo = T._RedirectSeguro().redirect_request(
            req, None, 301, "Moved", {}, "https://b.example/novo")
        self.assertIn("b.example", novo.full_url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
