import unittest
from main import crc8, request, response


class ProtocolTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(crc8(b'123456789'), 0xF4)

    def test_query(self):
        raw = bytes.fromhex(request(7, 9))
        self.assertEqual(raw[:4], bytes([7, 0, 9, 1]))
        self.assertEqual(raw[5], 1)

    def test_response_and_corruption(self):
        header = bytes([0, 7, 9, 13])
        payload = bytes([0]) + bytes(range(12))
        packet = header + bytes([crc8(header + payload)]) + payload
        self.assertEqual(response(packet.hex(), 7, 9), bytes(range(12)).hex().upper())
        with self.assertRaises(ValueError):
            response(packet.hex(), 8, 9)
        bad = bytearray(packet); bad[-1] ^= 1
        with self.assertRaises(ValueError):
            response(bad.hex(), 7, 9)


if __name__ == '__main__':
    unittest.main()
