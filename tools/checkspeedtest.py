"""Run the actual firmware wire handler on loopback, using the add-on client."""
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portall"))
import speedtest

KEY = "1" * 64
SOURCE = r'''
#include "protocol.h"
#include <netinet/in.h>
#include <unistd.h>
#include <signal.h>
int main() {
  signal(SIGPIPE, SIG_IGN);
  int s=socket(AF_INET,SOCK_STREAM,0);
  sockaddr_in a{}; a.sin_family=AF_INET; a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
  if(bind(s,(sockaddr*)&a,sizeof a)||listen(s,1)) return 1;
  socklen_t len=sizeof a; getsockname(s,(sockaddr*)&a,&len);
  printf("%u\n",ntohs(a.sin_port)); fflush(stdout);
  char buffer[esphome::portall_speedtest::CHUNK];
  for(;;) {
    int fd=accept(s,nullptr,nullptr); if(fd<0) return 2;
    esphome::portall_speedtest::serve_client(fd,
      "1111111111111111111111111111111111111111111111111111111111111111",buffer);
    shutdown(fd,SHUT_RDWR); close(fd);
  }
}
'''


class Test(unittest.TestCase):
    def test_wire(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=pathlib.Path(tmp)/"test.cpp"
            src.write_text(SOURCE)
            binary=pathlib.Path(tmp)/"test"
            subprocess.run(["g++","-std=c++17","-Wall","-Wextra","-Werror","-O2",
                "-I"+str(ROOT/"components/portall_speedtest"),str(src),"-o",str(binary)],check=True)
            p=subprocess.Popen([str(binary)],stdout=subprocess.PIPE,text=True)
            try:
                port=int(p.stdout.readline())
                with self.assertRaises(OSError):
                    speedtest.connect("127.0.0.1",port,"2"*64,"P")
                result=speedtest.measure("127.0.0.1",port,KEY)
                self.assertGreater(result["rx"],0)
                self.assertGreater(result["tx"],0)
                self.assertGreater(result["rtt"],0)
                self.assertLessEqual(result["rx_bytes"],speedtest.LIMIT)
                self.assertLessEqual(result["tx_bytes"],speedtest.LIMIT)
            finally:
                p.terminate()
                p.wait(timeout=5)
                p.stdout.close()

    def test_truncated_upload_is_rejected(self):
        class Fake:
            def __init__(self, replies=()):
                self.replies = iter(replies)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def close(self):
                pass
            def settimeout(self, seconds):
                pass
            def sendall(self, block):
                pass
            def shutdown(self, how):
                pass
            def recv(self, size):
                return next(self.replies, b"")
        class Sink(Fake):
            def __init__(self):
                self.sent = 0
                self.reply = None
            def sendall(self, block):
                self.sent += len(block)
            def shutdown(self, how):
                self.reply = iter(f"RX {self.sent} 1000\n".encode())
            def recv(self, size):
                return bytes([next(self.reply)])
        sink = Sink()
        upload = Fake([b"a" * 65536, b"a" * 65536, b""])
        with patch.object(speedtest, "LIMIT", 65536 * 2), patch.object(
            speedtest, "connect", side_effect=[(Fake(), 1)] * 5 + [(sink, 1), (upload, 1)]
        ):
            with self.assertRaisesRegex(OSError, "Incomplete upload"):
                speedtest.measure("localhost", 5201, KEY)

    def test_targets(self):
        test=speedtest.Speedtest([("Good",{"host":"panel.local","speedtest_key":KEY}),
                                   ("Off",{"host":"other.local"})])
        self.assertEqual(list(test.targets),["Good"])
        self.assertNotIn(KEY.encode(),test.page())
        with self.assertRaises(PermissionError):
            test.run({"panel":"Good","nonce":"bad"})
        with self.assertRaises(ValueError):
            test.run({"panel":"unconfigured","nonce":test.nonce})
        with patch.object(speedtest,"measure",return_value={"rx":1}) as m:
            self.assertEqual(test.run({"panel":"Good","nonce":test.nonce}),{"rx":1})
            m.assert_called_once_with("panel.local",5201,KEY)


if __name__=="__main__":
    unittest.main()
