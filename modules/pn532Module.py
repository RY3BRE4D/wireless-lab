#!/usr/bin/env python3
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

import board
import busio

from adafruit_pn532.i2c import PN532_I2C
import ndef
import re


class PN532Module:
	# ----------------------------
	# Init
	# ----------------------------
	def __init__(self, i2cAddress: int = 0x24, debug: bool = False):
		# Store Settings
		self.i2cAddress = i2cAddress
		self.debug = debug

		# Thread Safety For Flask Requests
		self._lock = threading.Lock()

		# PN532 Handle
		self._pn532 = None

		# Debounce To Avoid Re-Parsing Same Tag Repeatedly
		self._lastUidHex = None
		self._lastSeenMonotonic = 0.0
		self._debounceSeconds = 0.75

	def _log(self, msg: str) -> None:
		if self.debug:
			print(f"[PN532] {msg}")

	def init(self) -> Dict[str, Any]:
		with self._lock:
			if self._pn532 is not None:
				return {"ok": True, "alreadyInit": True}

			try:
				# Create I2C Bus
				i2c = busio.I2C(board.SCL, board.SDA)

				# Create PN532 Instance
				self._pn532 = PN532_I2C(i2c, debug=self.debug, address=self.i2cAddress)

				# Fetch Firmware
				ic, ver, rev, support = self._pn532.firmware_version
				self._log(f"Firmware IC={ic} Ver={ver}.{rev} Support=0x{support:02x}")

				# Configure Reader Mode
				self._pn532.SAM_configuration()

				return {
					"ok": True,
					"ic": ic,
					"version": ver,
					"revision": rev,
					"support": int(support),
					"i2cAddress": hex(self.i2cAddress),
				}

			except Exception as e:
				self._pn532 = None
				return {"ok": False, "error": str(e)}

	# ----------------------------
	# Basic Helpers
	# ----------------------------
	def _bytesToHex(self, b: Optional[bytes]) -> Optional[str]:
		if b is None:
			return None
		return "".join([f"{x:02x}" for x in b])

	def _hexToBytes(self, hexStr: str) -> bytes:
		hexStr = (hexStr or "").strip().replace(":", "").replace(" ", "")
		return bytes.fromhex(hexStr)

	def _debounceUid(self, uidHex: str) -> bool:
		now = time.monotonic()
		if self._lastUidHex == uidHex and (now - self._lastSeenMonotonic) < self._debounceSeconds:
			return True
		self._lastUidHex = uidHex
		self._lastSeenMonotonic = now
		return False

	# ----------------------------
	# Scan + Classification
	# ----------------------------
	def scanOnce(self, timeoutSeconds: float = 0.35) -> Dict[str, Any]:
		"""
		Read One ISO14443A Target UID.
		"""
		with self._lock:
			if self._pn532 is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			try:
				uid = self._pn532.read_passive_target(timeout=timeoutSeconds)
				if uid is None:
					return {"ok": True, "found": False}

				uidHex = self._bytesToHex(uid)
				debounced = self._debounceUid(uidHex)

				# Try ATS If Available (Not Always)
				ats = None
				try:
					ats = self._pn532.get_ats()
				except Exception:
					ats = None

				classification = self.classify(uidBytes=uid, atsBytes=ats)

				return {
					"ok": True,
					"found": True,
					"debounced": debounced,
					"uidHex": uidHex,
					"uidLen": len(uid),
					"atsHex": self._bytesToHex(ats),
					"classification": classification,
				}

			except Exception as e:
				return {"ok": False, "error": str(e)}

	def classify(self, uidBytes: Optional[bytes], atsBytes: Optional[bytes] = None) -> Dict[str, Any]:
		"""
		Best-Effort Classification.
		"""
		uidLen = len(uidBytes) if uidBytes else 0
		hasAts = atsBytes is not None and len(atsBytes) > 0

		# ISO14443-4 Often Presents ATS
		if hasAts:
			return {
				"protocol": "ISO14443A",
				"layer4": True,
				"familyGuess": "Type 4 / ISO-DEP Candidate",
				"confidence": "low",
			}

		# UID Length Heuristic (Not Reliable, Just A Hint)
		if uidLen == 4:
			return {
				"protocol": "ISO14443A",
				"layer4": False,
				"familyGuess": "Type 2 / MIFARE-Like Candidate (4-Byte UID)",
				"confidence": "low",
			}

		if uidLen == 7:
			return {
				"protocol": "ISO14443A",
				"layer4": False,
				"familyGuess": "Type 2 / Type 4 Candidate (7-Byte UID)",
				"confidence": "low",
			}

		return {
			"protocol": "ISO14443A",
			"layer4": False,
			"familyGuess": "Unknown ISO14443A Tag",
			"confidence": "low",
		}

	# ----------------------------
	# Capabilities / Probe
	# ----------------------------
	def probeCapabilities(self) -> Dict[str, Any]:
		"""
		Probe What We Can Do WITHOUT Any Secrets/Keys.
		Returns A Unified Capability Report.
		"""
		scan = self.scanOnce(timeoutSeconds=0.20)
		if not scan.get("ok") or not scan.get("found"):
			return scan

		report: Dict[str, Any] = {
			"ok": True,
			"found": True,
			"uidHex": scan.get("uidHex"),
			"uidLen": scan.get("uidLen"),
			"atsHex": scan.get("atsHex"),
			"classification": scan.get("classification"),
			"capabilities": {
				"canReadNdef": False,
				"canWriteNdefText": False,
				"type2ConfigReadable": False,
				"type2LikelyLocked": None,
				"classicReadableWithCommonKeys": False,
				"classicWritableWithCommonKeys": False,
			},
			"lazySecuritySignals": [],
			"riskScore": 0,
		}

		# Try Read Type 2 Config / Lock State (If Driver Supports ntag2xx_* Calls)
		type2Info = self._probeType2ConfigAndLocks()
		if type2Info.get("ok"):
			report["capabilities"]["type2ConfigReadable"] = True
			report["capabilities"]["type2LikelyLocked"] = type2Info.get("likelyLocked")
			report["type2Info"] = type2Info

		# Try Read NDEF (Type2 First, Then Classic Best-Effort)
		ndefRead = self.tryReadNdef()
		report["ndefProbe"] = ndefRead
		if ndefRead.get("ok") and ndefRead.get("hasNdef"):
			report["capabilities"]["canReadNdef"] = True
			report["ndef"] = ndefRead

		# Heuristic: If Type2 And Not Locked, Writing Text Might Work
		if report["capabilities"]["type2ConfigReadable"] and report["capabilities"]["type2LikelyLocked"] is False:
			report["capabilities"]["canWriteNdefText"] = True

		# Classic Heuristic: If We Can Authenticate Any Sector With Common Keys, Mark Readable
		if ndefRead.get("ok") and ndefRead.get("tagFamily", "").startswith("MIFARE Classic"):
			report["capabilities"]["classicReadableWithCommonKeys"] = bool(ndefRead.get("classicAuthMap"))
			# Writable Is Only A Guess: If We Authenticated Anything, Writes Might Also Work
			report["capabilities"]["classicWritableWithCommonKeys"] = bool(ndefRead.get("classicAuthMap"))

		# Lazy Security Detector (Heuristics)
		signals, score = self._detectLazySecuritySignals(scanResult=scan, type2Info=type2Info)
		report["lazySecuritySignals"] = signals
		report["riskScore"] = score

		return report

	def _probeType2ConfigAndLocks(self) -> Dict[str, Any]:
		"""
		Safe Read Of Type 2 CC / Lock Bytes (Public Pages).
		Works Only If Library Exposes ntag2xx_read_block.
		"""
		with self._lock:
			if self._pn532 is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			if not hasattr(self._pn532, "ntag2xx_read_block"):
				return {"ok": False, "error": "Driver Has No ntag2xx_read_block"}

			try:
				# CC Page Is Usually Block 3 For Type 2
				cc = self._pn532.ntag2xx_read_block(3)
				if cc is None:
					return {"ok": False, "error": "No CC Read"}

				# Type 2 CC Usually Starts With 0xE1
				isType2Ndef = (len(cc) > 0 and cc[0] == 0xE1)

				# Read Lock Bytes In Page 2 For Most NTAG/Ultralight
				page2 = self._pn532.ntag2xx_read_block(2) or bytes([0, 0, 0, 0])

				# Lock Bytes Are Usually Byte 2..3 Of Page 2, But Tags Vary
				lockBytes = list(page2[2:4]) if len(page2) >= 4 else [0, 0]
				likelyLocked = any([b != 0x00 for b in lockBytes])

				return {
					"ok": True,
					"isType2NdefCandidate": bool(isType2Ndef),
					"ccHex": self._bytesToHex(cc),
					"page2Hex": self._bytesToHex(page2),
					"lockLikeBytes": lockBytes,
					"likelyLocked": bool(likelyLocked),
				}

			except Exception as e:
				return {"ok": False, "error": str(e)}

	# ----------------------------
	# MIFARE Classic Helpers
	# ----------------------------
	def _classicCommonKeys(self) -> List[Tuple[str, bytes]]:
		# Common Keys Seen In Factory Tags, Tutorials, And Some Apps
		return [
			# Factory Default (Most Common)
			("FFFFFFFFFFFF", bytes([0xFF] * 6)),
			# All Zeros
			("000000000000", bytes([0x00] * 6)),
			## NXP Transport Key (MAD)
			("A0A1A2A3A4A5", bytes([0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5])),
			# NXP MAD Key B (Common On Sector 0)
			("B0B1B2B3B4B5", bytes([0xB0, 0xB1, 0xB2, 0xB3, 0xB4, 0xB5])),
			# NDEF Key (Seen In Some NFC Apps)
			("D3F7D3F7D3F7", bytes([0xD3, 0xF7, 0xD3, 0xF7, 0xD3, 0xF7])),
			# Known From Public Research / Academic Material
			("4D3A99C351DD", bytes([0x4D, 0x3A, 0x99, 0xC3, 0x51, 0xDD])),
			("1A982C7E459A", bytes([0x1A, 0x98, 0x2C, 0x7E, 0x45, 0x9A])),
			# Common "Lazy Config" Patterns
			("ABCDEF123456", bytes([0xAB, 0xCD, 0xEF, 0x12, 0x34, 0x56])),
			("A1B2C3D4E5F6", bytes([0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6])),
			("FFFFFFFF0000", bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00])),
			("0000FFFFFFFF", bytes([0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])),
		        # Repeating Byte Patterns
		        ("010101010101", bytes([0x01] * 6)),
		        ("020202020202", bytes([0x02] * 6)),
		        ("A1A2A3A4A5A6", bytes([0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6])),
		]

	def _crc8Mad(self, data: bytes) -> int:
		"""
		CRC-8 For MAD (Polynomial 0x1D, Init 0xC7) Used By MIFARE Classic MAD.
		"""
		crc = 0xC7
		for b in data:
			crc ^= b
			for _ in range(8):
				if crc & 0x80:
					crc = ((crc << 1) ^ 0x1D) & 0xFF
				else:
					crc = (crc << 1) & 0xFF
		return crc

	def _classicBuildMad1(self, aid: int = 0x03E1, infoByte: int = 0x01):
		"""
		Build MAD1 Blocks For A 1K Tag.
		- block1[0] = CRC8 Over (block1[1:16] + block2[0:16])
		- block1[1] = infoByte
		- AIDs For Sectors 1..15 Are Stored Little-Endian.
		"""
		aidLo = aid & 0xFF
		aidHi = (aid >> 8) & 0xFF

		aidBytes = bytearray()
		for _ in range(15):
			aidBytes += bytes([aidLo, aidHi])  # Sectors 1..15

		b1 = bytearray([0x00] * 16)
		b2 = bytearray([0x00] * 16)

		b1[1] = infoByte & 0xFF
		b1[2:16] = aidBytes[0:14]     # 7 AIDs
		b2[0:16] = aidBytes[14:30]    # 8 AIDs

		b1[0] = self._crc8Mad(bytes(b1[1:16] + b2[0:16]))
		return bytes(b1), bytes(b2)

	def _classicFormatForNdefMad1(self, uid: bytes) -> Dict[str, Any]:
		"""
		Format MIFARE Classic 1K For NFC Forum NDEF:
		- Sector 0: Write MAD1 (Blocks 1-2) And Set Sector 0 Trailer (MAD Key/Access)
		- Sectors 1-15: Set Trailers To NFC/NDEF Readable Settings
		"""
		if self._pn532 is None:
			return {"ok": False, "error": "PN532 Not Initialized"}

		if not hasattr(self._pn532, "mifare_classic_authenticate_block") or not hasattr(self._pn532, "mifare_classic_write_block"):
			return {"ok": False, "error": "Driver Has No mifare_classic_* Support"}

		madKeyA  = bytes([0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5])
		ndefKeyA = bytes([0xD3, 0xF7, 0xD3, 0xF7, 0xD3, 0xF7])
		keyB     = bytes([0xFF] * 6)

		madAccess  = bytes([0x78, 0x77, 0x88])
		madGPB     = 0xC1
		ndefAccess = bytes([0x7F, 0x07, 0x88])
		ndefGPB    = 0x40

		MIFARE_CMD_AUTH_B = 0x61
		MIFARE_CMD_AUTH_A = 0x60

		# Auth Sector 0 With Default Key B (FF..)
		if not self._pn532.mifare_classic_authenticate_block(uid, 1, MIFARE_CMD_AUTH_B, keyB):
			return {"ok": False, "error": "Auth Failed On Sector 0 (KeyB FF..). Card May Be Locked."}

		madB1, madB2 = self._classicBuildMad1(aid=0x03E1, infoByte=0x01)
		if not self._pn532.mifare_classic_write_block(1, madB1):
			return {"ok": False, "error": "Write Failed (Sector 0 Block 1 MAD1)"}
		if not self._pn532.mifare_classic_write_block(2, madB2):
			return {"ok": False, "error": "Write Failed (Sector 0 Block 2 MAD1)"}

		# Sector 0 Trailer
		madTrailer = bytes(madKeyA + madAccess + bytes([madGPB]) + keyB)
		if not self._pn532.mifare_classic_write_block(3, madTrailer):
			return {"ok": False, "error": "Write Failed (Sector 0 Trailer)"}

		# NFC Sector Trailers (1..15)
		ndefTrailer = bytes(ndefKeyA + ndefAccess + bytes([ndefGPB]) + keyB)

		for sector in range(1, 16):
			trailerBlock = sector * 4 + 3
			if not self._pn532.mifare_classic_authenticate_block(uid, trailerBlock, MIFARE_CMD_AUTH_B, keyB):
				return {"ok": False, "error": f"Auth Failed On Sector {sector} Trailer (KeyB FF..)."}
			if not self._pn532.mifare_classic_write_block(trailerBlock, ndefTrailer):
				return {"ok": False, "error": f"Write Failed On Sector {sector} Trailer."}

		return {"ok": True, "formatted": True}

	def _classicAuthSectorBestEffort(
		self,
		uid: bytes,
		sector: int,
		keys: Optional[List[Tuple[str, bytes]]] = None,
	) -> Optional[Dict[str, Any]]:
		"""
		Best-Effort: Find A Working Key For A Sector.
		Returns { keyName, keyType, keyBytes, authCmd } Or None.
		authCmd Is The Exact Value That Succeeded (Driver-Dependent).
		"""
		if keys is None:
			keys = self._classicCommonKeys()

		if not hasattr(self._pn532, "mifare_classic_authenticate_block"):
			return None

		# Some PN532 Python Libs Expect:
		# - Key A/B As 0x60/0x61 (MIFARE Auth Commands)
		# Others Expect:
		# - Key A/B As 0/1
		#
		# We'll Try Both To Be Compatible.
		AUTH_VARIANTS = [
			("A", 0x60),
			("A", 0),
			("B", 0x61),
			("B", 1),
		]

		# Auth Against A Safe Block In The Sector.
		# Block 0 Is Manufacturer, But Auth Should Still Work There.
		# Still, Using Block 1 For Sector 0 Avoids Edge-Case Driver Weirdness.
		firstBlock = sector * 4
		authBlock = (firstBlock + 1) if sector == 0 else firstBlock

		for keyName, keyBytes in keys:
			for keyType, cmd in AUTH_VARIANTS:
				try:
					ok = self._pn532.mifare_classic_authenticate_block(uid, authBlock, cmd, keyBytes)
					if ok:
						return {"keyName": keyName, "keyType": keyType, "key": keyBytes, "authCmd": cmd, "authBlock": authBlock}
				except Exception:
					pass

				# Reselect After Failure
				try:
					self._pn532.read_passive_target(timeout=0.05)
				except Exception:
					pass
		return None


	def _classicReadAllDataBlocksBestEffort(
		self,
		uid: bytes,
		keys: Optional[List[Tuple[str, bytes]]] = None,
		startSector: int = 0,
	) -> Dict[str, Any]:
		"""
		Best-Effort: Read Data Blocks (Not Trailers) For All Sectors We Can Auth.
		Returns { ok, authMap, blocksHex, dataBytes }.
		"""
		if not hasattr(self._pn532, "mifare_classic_read_block") or not hasattr(self._pn532, "mifare_classic_authenticate_block"):
			return {"ok": False, "error": "Driver Has No mifare_classic_* Support"}

		if keys is None:
			keys = self._classicCommonKeys()

		MIFARE_CMD_AUTH_A = 0x60
		MIFARE_CMD_AUTH_B = 0x61

		authMap: Dict[int, Dict[str, Any]] = {}
		blocksHex: Dict[int, str] = {}
		data = bytearray()

		for sector in range(startSector, 16):
			firstBlock = sector * 4
			trailerBlock = firstBlock + 3

			# Find A Key For This Sector
			authInfo = self._classicAuthSectorBestEffort(uid=uid, sector=sector, keys=keys)
			if not authInfo:
				continue

			authMap[sector] = {"keyName": authInfo["keyName"], "keyType": authInfo["keyType"]}

			# Re-Auth With The Found KeyType/Key (Keeps State Consistent)
			cmd = MIFARE_CMD_AUTH_A if authInfo["keyType"] == "A" else MIFARE_CMD_AUTH_B
			ok = self._pn532.mifare_classic_authenticate_block(uid, firstBlock, cmd, authInfo["key"])
			if not ok:
				continue


			# Read 3 Data Blocks (Skip Trailer)
			for b in range(firstBlock, trailerBlock):
				try:
					blk = self._pn532.mifare_classic_read_block(b)
				except Exception:
					blk = None
				if blk is None:
					continue
				blocksHex[b] = self._bytesToHex(blk)
				data.extend(blk)

		return {"ok": True, "authMap": authMap, "blocksHex": blocksHex, "dataBytes": bytes(data)}

	def _classicFindNdefTlv(self, data: bytes):
		i = 0
		n = len(data)

		while i < n:
			t = data[i]

			if t == 0x00:  # NULL TLV
				i += 1
				continue

			if t == 0xFE:  # Terminator TLV
				break

			if i + 1 >= n:
				break

			l = data[i + 1]
			if l == 0xFF:
				if i + 3 >= n:
					break
				tlvLen = (data[i + 2] << 8) | data[i + 3]
				valueStart = i + 4
			else:
				tlvLen = l
				valueStart = i + 2

			valueEnd = valueStart + tlvLen
			if valueEnd > n:
				i += 1
				continue

			if t == 0x03:  # NDEF TLV
				if tlvLen == 0:
					i = valueEnd
					continue

				first = data[valueStart]
				looksLikeRecordHeader = (first & 0x80) != 0  # MB Bit
				notGarbage = first not in (0x00, 0x03, 0xE1, 0xFE)

				if looksLikeRecordHeader and notGarbage:
					return valueStart, tlvLen

				# Skip This TLV And Keep Scanning
				i += 1
				continue

			# Other TLVs: Skip
			i = valueEnd

		return None, None


	def _tryReadMifareClassicNdef(self, uid: bytes) -> Dict[str, Any]:
		"""
		Best-Effort: Authenticate With Common Keys Per Sector And Search For NDEF TLV (0x03).
		This Does NOT Guarantee Success If The Card Uses Unknown Keys.
		"""
		# Sector 0 Block 0 Is Always The Manufacturer Block (UID/SAK/ATQA).
		# Its Bytes Are Not TLV Data And Will Corrupt The TLV Parser If Included.
		# NDEF On MIFARE Classic Is Always Stored In Sector 1+.
		readRes = self._classicReadAllDataBlocksBestEffort(uid=uid, startSector=1)
		if not readRes.get("ok"):
			return readRes

		authMap = readRes.get("authMap") or {}
		dataBytes = readRes.get("dataBytes") or b""

		if not authMap:
			return {"ok": True, "hasNdef": False, "note": "Classic Auth Failed With Common Keys"}

		ndefStart, ndefLen = self._classicFindNdefTlv(dataBytes)
		if ndefStart is None or ndefLen is None:
			return {
				"ok": True,
				"hasNdef": False,
				"note": "No NDEF TLV Found In Readable Data Blocks",
				"tagFamily": "MIFARE Classic (Best-Effort)",
				"classicAuthMap": authMap,
			}

		rawNdef = bytes(dataBytes[ndefStart : ndefStart + ndefLen])

		# Decode NDEF Records
		# Decode NDEF Records
		try:
			decoded = []

			for rec in ndef.message_decoder(rawNdef):

				# Text Record
				if isinstance(rec, ndef.TextRecord):
					decoded.append({
						"type": "text",
						"text": rec.text,
						"lang": rec.language,
					})

				# URI Record
				elif isinstance(rec, ndef.UriRecord):
					decoded.append({
						"type": "uri",
						"uri": rec.uri,
					})

				# Any Other Record Type
				else:
					decoded.append({
						"type": "other",
						"tnf": int(getattr(rec, "tnf", -1)),
						"recordType": (
							rec.type.decode("utf-8", "ignore")
							if isinstance(getattr(rec, "type", b""), (bytes, bytearray))
							else str(getattr(rec, "type", ""))
						),
						"id": (
							rec.id.decode("utf-8", "ignore")
							if isinstance(getattr(rec, "id", b""), (bytes, bytearray))
							else str(getattr(rec, "id", ""))
						),
						"payloadHex": (
							rec.payload.hex()
							if isinstance(getattr(rec, "payload", b""), (bytes, bytearray))
							else str(getattr(rec, "payload", ""))
						),
					})

			return {
				"ok": True,
				"hasNdef": True,
				"tagFamily": "MIFARE Classic (Best-Effort)",
				"classicAuthMap": authMap,
				"ndefHex": self._bytesToHex(rawNdef),
				"records": decoded,
			}

		except Exception as e:
			return {
				"ok": True,
				"hasNdef": True,
				"tagFamily": "MIFARE Classic (Best-Effort)",
				"classicAuthMap": authMap,
				"ndefHex": self._bytesToHex(rawNdef),
				"decodeError": str(e),
			}

	# ----------------------------
	# NDEF Read/Write (Safe)
	# ----------------------------
	def tryReadNdef(self) -> Dict[str, Any]:
		"""
		Read NDEF TLV For Type 2 Tags (Best-Effort),
		With Fallback Attempt For MIFARE Classic.
		"""
		with self._lock:
			if self._pn532 is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			hasType2 = hasattr(self._pn532, "ntag2xx_read_block")

			try:
				# Select Tag First — ntag2xx_read_block Requires An Active Target
				uid = self._pn532.read_passive_target(timeout=2.0)
				if uid is None:
					return {"ok": True, "hasNdef": False}

				# ----------------------------
				# Try Type 2 Path First (If Available)
				# ----------------------------
				if hasType2:
					cc = self._pn532.ntag2xx_read_block(3)

					# Type 2 Capability Container Starts With 0xE1
					if cc is not None and len(cc) > 0 and cc[0] == 0xE1:
						# Read A Chunk Of Memory For TLV Parse (Keep It Bounded)
						data = bytearray()
						for blockIndex in range(4, 4 + 64):
							block = self._pn532.ntag2xx_read_block(blockIndex)
							if block is None:
								break
							data.extend(block)

						ndefStart, ndefLen = self._classicFindNdefTlv(bytes(data))
						if ndefStart is None or ndefLen is None:
							return {"ok": True, "hasNdef": False}

						rawMsg = bytes(data[ndefStart : ndefStart + ndefLen])

						records = []
						try:
							for rec in ndef.message_decoder(rawMsg):
								# Text Record
								if isinstance(rec, ndef.TextRecord):
									records.append({
										"type": "text",
										"text": rec.text,
										"lang": rec.language,
									})

								# URI Record
								elif isinstance(rec, ndef.UriRecord):
									records.append({
										"type": "uri",
										"uri": rec.uri,
									})

								# Any Other Record Type
								else:
									records.append({
										"type": "other",
										"tnf": rec.tnf,
										"recordType": (
											rec.type.decode(errors="ignore")
											if isinstance(rec.type, (bytes, bytearray))
											else str(rec.type)
										),
										"payloadHex": (
											rec.payload.hex()
											if isinstance(rec.payload, (bytes, bytearray))
											else str(rec.payload)
										),
									})

							return {
								"ok": True,
								"hasNdef": True,
								"tagFamily": "Type 2 (NTAG/Ultralight-Like)",
								"ndefHex": self._bytesToHex(rawMsg),
								"records": records,
							}

						except Exception as e:
							return {"ok": False, "error": f"NDEF Decode Failed: {e}"}

				# ----------------------------
				# Fallback: MIFARE Classic Best-Effort
				# ----------------------------
				return self._tryReadMifareClassicNdef(uid)

			except Exception as e:
				return {"ok": False, "error": str(e)}

	def tryWriteNdefText(self, text: str, language: str = "en") -> Dict[str, Any]:
		"""
		Try Writing A Text Record.
		- Primary: Type 2 (ntag2xx_write_block)
		- Fallback: MIFARE Classic (Sector 1 Data Blocks) If We Can Auth With Common Keys
		"""
		with self._lock:
			if self._pn532 is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			# Build NDEF Message
			textRec = ndef.TextRecord(text, language=language)
			rawMsg = b"".join(ndef.message_encoder([textRec]))

			if len(rawMsg) > 0xFE:
				return {"ok": False, "error": "NDEF Message Too Long"}

			# Wrap In TLV
			tlv = bytearray()
			tlv.append(0x03)
			tlv.append(len(rawMsg))
			tlv.extend(rawMsg)
			tlv.append(0xFE)

			# ----------------------------
			# Type 2 Write (Preferred)
			# ----------------------------
			if hasattr(self._pn532, "ntag2xx_write_block") and hasattr(self._pn532, "ntag2xx_read_block"):
				try:
					# Type 2 CC Check
					cc = self._pn532.ntag2xx_read_block(3)
					if not (cc is not None and len(cc) > 0 and cc[0] == 0xE1):
						raise Exception("Not A Type 2 Tag (CC Page 3 Does Not Start With 0xE1)")

					# CC[2] Is Size In 8-Byte Units For Type 2 Tags
					# This Is The Size Of The Data Area Starting At Page 4
					dataBytes = cc[2] * 8

					# Check BEFORE Padding (Padding Is Just For Page Alignment)
					tlvLenUnpadded = len(tlv)
					if tlvLenUnpadded > dataBytes:
						return {
							"ok": False,
							"error": f"NDEF Too Large For Type 2 Tag (Need {tlvLenUnpadded} Bytes, Tag Has {dataBytes} Bytes)",
						}

					# Pad To 4 Bytes (Page Writes)
					while len(tlv) % 4 != 0:
						tlv.append(0x00)

					# Write Starting At Page 4
					page = 4
					for i in range(0, len(tlv), 4):
						chunk = bytes(tlv[i : i + 4])
						ok = self._pn532.ntag2xx_write_block(page, chunk)
						if not ok:
							raise Exception(f"Type2 Write Failed At Page {page}")
						page += 1

					# Read-Back Verify (Simple)
					verify = self._pn532.ntag2xx_read_block(4)
					if verify is None:
						raise Exception("Type2 Verify Read Failed At Page 4")

					return {"ok": True, "tagFamily": "Type 2 (NTAG/Ultralight-Like)"}

				except Exception as e:
					self._log(f"Type2 Write Skipped/Failed: {e}")
					# Fall Through To Classic Attempt


			# ----------------------------
			# Classic Write (Fallback)
			# ----------------------------
			if not hasattr(self._pn532, "mifare_classic_write_block"):
				return {"ok": False, "error": "No Type2 Write Support And No Classic Write Support"}

			try:
				uid = self._pn532.read_passive_target(timeout=0.30)
				if uid is None:
					return {"ok": False, "error": "No Tag"}

				# We Write TLV Into Sector 1 (Blocks 4, 5, 6). Trailer Is Block 7.
				# Example: (0x03, len, NDEF..., 0xFE).
				sector = 1
				authInfo = self._classicAuthSectorBestEffort(uid=uid, sector=sector)
				if not authInfo:
					return {"ok": False, "error": "Classic Auth Failed With Common Keys (Cannot Write)"}

				# Authenticate Before Writes
				MIFARE_CMD_AUTH_A = 0x60
				MIFARE_CMD_AUTH_B = 0x61
				firstBlock = sector * 4
				cmd = MIFARE_CMD_AUTH_A if authInfo["keyType"] == "A" else MIFARE_CMD_AUTH_B
				ok = self._pn532.mifare_classic_authenticate_block(uid, firstBlock, cmd, authInfo["key"])
				if not ok:
					return {"ok": False, "error": "Classic Auth Failed (Cannot Write)"}

				# Pad To 16-Byte Blocks (Classic Blocks Are 16 Bytes)
				while len(tlv) % 16 != 0:
					tlv.append(0x00)

				# Write Blocks 4..6 (48 Bytes)
				blocks = [4, 5, 6]
				needed = len(blocks) * 16
				payload = bytes(tlv[:needed]).ljust(needed, b"\x00")

				for i, blockNum in enumerate(blocks):
					chunk = payload[i * 16 : (i + 1) * 16]
					ok = self._pn532.mifare_classic_write_block(blockNum, chunk)
					if not ok:
						return {"ok": False, "error": f"Classic Write Failed At Block {blockNum}"}

				return {"ok": True, "tagFamily": "MIFARE Classic (Best-Effort)", "authKey": authInfo["keyName"], "authKeyType": authInfo["keyType"]}

			except Exception as e:
				return {"ok": False, "error": str(e)}
			
	def tryWriteNdefUri(self, uri: str) -> Dict[str, Any]:
		uri = (uri or "").strip()
		if not uri:
			return {"ok": False, "error": "Missing URI"}

		# Add Scheme If Missing
		if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", uri):
			uri = "http://" + uri

		# Build NDEF Message (Single URI Record; No AAR)
		try:
			msg = b"".join(ndef.message_encoder([ndef.UriRecord(uri)]))
		except Exception as e:
			return {"ok": False, "error": f"NDEF Encode Failed: {e}"}

		# TLV: 03 len <msg> FE (Handle Long Lengths Too)
		tlv = bytearray([0x03])
		if len(msg) < 0xFF:
			tlv.append(len(msg) & 0xFF)
		else:
			tlv.append(0xFF)
			tlv.append((len(msg) >> 8) & 0xFF)
			tlv.append(len(msg) & 0xFF)
		tlv += msg
		tlv.append(0xFE)

		with self._lock:
			if self._pn532 is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			# --- Type 2 Path: CC Check + Write ---
			if hasattr(self._pn532, "ntag2xx_write_block") and hasattr(self._pn532, "ntag2xx_read_block"):
				try:
					cc = self._pn532.ntag2xx_read_block(3)
					if not (cc and cc[0] == 0xE1):
						raise Exception("Not Type 2")
					dataBytes = cc[2] * 8
					if len(tlv) > dataBytes:
						return {"ok": False, "error": f"NDEF Too Large (Need {len(tlv)} Bytes, Tag Has {dataBytes} Bytes)"}
					while len(tlv) % 4 != 0:
						tlv.append(0x00)
					page = 4
					for i in range(0, len(tlv), 4):
						if not self._pn532.ntag2xx_write_block(page, bytes(tlv[i:i+4])):
							return {"ok": False, "error": f"Type2 Write Failed At Page {page}"}
						page += 1
					return {"ok": True, "tagFamily": "Type 2", "uri": uri}
				except Exception:
					pass  # Fall Through To Classic

			# --- Classic Path: Format First, Then Write ---
			if not hasattr(self._pn532, "mifare_classic_write_block") or not hasattr(self._pn532, "mifare_classic_authenticate_block"):
				return {"ok": False, "error": "No Classic Write Support"}

			uid = self._pn532.read_passive_target(timeout=0.30)
			if uid is None:
				return {"ok": False, "error": "No Tag"}

			fmt = self._classicFormatForNdefMad1(uid)
			if not fmt.get("ok"):
				return fmt

			# Classic 1K NDEF Capacity (Sectors 1..15, 3 Data Blocks Each): 720 Bytes
			capacity = 15 * 3 * 16
			if len(tlv) > capacity:
				return {"ok": False, "error": f"NDEF Too Large For Classic 1K (Need {len(tlv)} Bytes, Has {capacity} Bytes)"}

			while len(tlv) % 16 != 0:
				tlv.append(0x00)

			MIFARE_CMD_AUTH_A = 0x60
			ndefKeyA = bytes([0xD3, 0xF7, 0xD3, 0xF7, 0xD3, 0xF7])

			offset = 0
			for sector in range(1, 16):
				firstBlock = sector * 4
				trailerBlock = firstBlock + 3

				if not self._pn532.mifare_classic_authenticate_block(uid, firstBlock, MIFARE_CMD_AUTH_A, ndefKeyA):
					return {"ok": False, "error": f"Auth Failed On Sector {sector} (NDEF KeyA)"}

				for b in range(firstBlock, trailerBlock):  # 3 Data Blocks
					chunk = bytes(tlv[offset:offset+16])
					if not self._pn532.mifare_classic_write_block(b, chunk):
						return {"ok": False, "error": f"Write Failed At Block {b}"}
					offset += 16
					if offset >= len(tlv):
						return {"ok": True, "tagFamily": "MIFARE Classic (NDEF)", "uri": uri}

			return {"ok": False, "error": "Unexpected: Ran Out Of Sectors"}

	# ----------------------------
	# Classic Dump / Wipe (Best-Effort)
	# ----------------------------
	def dumpMifareClassic(self, includeTrailers: bool = False) -> Dict[str, Any]:
		"""
		Dump A MIFARE Classic 1K Best-Effort.
		- Reads Only Sectors We Can Authenticate With Common Keys
		- By Default Skips Trailer Blocks (Key Material)
		"""
		with self._lock:
			if self._pn532 is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			if not hasattr(self._pn532, "mifare_classic_read_block") or not hasattr(self._pn532, "mifare_classic_authenticate_block"):
				return {"ok": False, "error": "Driver Has No mifare_classic_* Support"}

			try:
				uid = self._pn532.read_passive_target(timeout=0.30)
				if uid is None:
					return {"ok": True, "found": False}

				uidHex = self._bytesToHex(uid)
				keys = self._classicCommonKeys()

				MIFARE_CMD_AUTH_A = 0x60
				MIFARE_CMD_AUTH_B = 0x61

				authMap: Dict[int, Dict[str, Any]] = {}
				blocks: Dict[int, str] = {}

				for sector in range(16):
					firstBlock = sector * 4
					trailerBlock = firstBlock + 3

					authInfo = self._classicAuthSectorBestEffort(uid=uid, sector=sector, keys=keys)
					if not authInfo:
						continue

					authMap[sector] = {"keyName": authInfo["keyName"], "keyType": authInfo["keyType"]}

					ok = self._pn532.mifare_classic_authenticate_block(uid, authInfo.get("authBlock", firstBlock), authInfo["authCmd"], authInfo["key"])
					if not ok:
						continue

					lastBlock = trailerBlock if includeTrailers else (trailerBlock - 1)
					for b in range(firstBlock, lastBlock + 1):
						try:
							blk = self._pn532.mifare_classic_read_block(b)
						except Exception:
							blk = None
						if blk is None:
							continue
						blocks[b] = self._bytesToHex(blk)

				return {
					"ok": True,
					"found": True,
					"uidHex": uidHex,
					"tagFamily": "MIFARE Classic (Best-Effort)",
					"includeTrailers": bool(includeTrailers),
					"classicAuthMap": authMap,
					"blocksHex": blocks,
				}

			except Exception as e:
				return {"ok": False, "error": str(e)}

	def wipeMifareClassicToFactory(self, resetKeys: bool = True, wipeData: bool = True) -> Dict[str, Any]:
		"""
		Best-Effort Wipe For MIFARE Classic 1K.
		- Only Touches Sectors We Can Authenticate With Common Keys
		- Skips Manufacturer Block 0
		- resetKeys=True Restores Trailer To Factory Default (FF.. / FF078069 / FF..)
		"""
		with self._lock:
			if self._pn532 is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			if not hasattr(self._pn532, "mifare_classic_write_block") or not hasattr(self._pn532, "mifare_classic_authenticate_block"):
				return {"ok": False, "error": "Driver Has No mifare_classic_* Support"}

			try:
				uid = self._pn532.read_passive_target(timeout=0.30)
				if uid is None:
					return {"ok": True, "found": False}

				uidHex = self._bytesToHex(uid)

				keys = self._classicCommonKeys()
				MIFARE_CMD_AUTH_A = 0x60
				MIFARE_CMD_AUTH_B = 0x61

				# Factory Trailer Layout: KeyA(6) + Access(4) + KeyB(6)
				factoryTrailer = bytes([0xFF] * 6 + [0xFF, 0x07, 0x80, 0x69] + [0xFF] * 6)
				zeroBlock = bytes([0x00] * 16)

				changed = {"dataBlocks": 0, "trailers": 0, "sectorsTouched": 0}
				touchedSectors: List[int] = []

				for sector in range(16):
					firstBlock = sector * 4
					trailerBlock = firstBlock + 3

					# Never Write Manufacturer Block 0 (Unless Magic Tag)
					if sector == 0:
						firstWritable = 1
					else:
						firstWritable = firstBlock

					authInfo = self._classicAuthSectorBestEffort(uid=uid, sector=sector, keys=keys)
					if not authInfo:
						continue

					cmd = MIFARE_CMD_AUTH_A if authInfo["keyType"] == "A" else MIFARE_CMD_AUTH_B
					ok = self._pn532.mifare_classic_authenticate_block(uid, firstBlock, cmd, authInfo["key"])
					if not ok:
						continue

					touchedSectors.append(sector)
					changed["sectorsTouched"] += 1

					# Wipe Data Blocks
					if wipeData:
						for b in range(firstWritable, trailerBlock):
							ok = self._pn532.mifare_classic_write_block(b, zeroBlock)
							if ok:
								changed["dataBlocks"] += 1

					# Reset Trailer (Keys + Access Bits)
					if resetKeys:
						ok = self._pn532.mifare_classic_write_block(trailerBlock, factoryTrailer)
						if ok:
							changed["trailers"] += 1

				return {
					"ok": True,
					"found": True,
					"uidHex": uidHex,
					"tagFamily": "MIFARE Classic (Best-Effort)",
					"note": "Only Sectors Authenticated With Common Keys Were Modified",
					"changed": changed,
					"touchedSectors": touchedSectors,
				}

			except Exception as e:
				return {"ok": False, "error": str(e)}

	# ----------------------------
	# Lazy Security Detector (Heuristics)
	# ----------------------------
	def _detectLazySecuritySignals(self, scanResult: Dict[str, Any], type2Info: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
		"""
		Heuristic Signals That Often Correlate With Lazy Designs.
		These Do Not Prove Vulnerability; They Flag “Worth Reviewing”.
		"""
		signals: List[Dict[str, Any]] = []
		score = 0

		uidLen = scanResult.get("uidLen") or 0
		atsHex = scanResult.get("atsHex")

		# Short UID Often Correlates With Cheap "UID Only" Credential Checks
		if uidLen == 4:
			signals.append({
				"id": "short_uid",
				"severity": "medium",
				"why": "4-Byte UID Often Gets Used As The Only Credential In Cheap Systems",
			})
			score += 2

		# No ATS Often Means No ISO-DEP Session (Not Always Bad, Just Simpler)
		if not atsHex:
			signals.append({
				"id": "no_ats",
				"severity": "low",
				"why": "No ATS Suggests No ISO14443-4 Session (Often Simpler Tag Interaction)",
			})
			score += 1

		# Type2 Candidate But CC Missing/Weird
		if type2Info.get("ok") and not type2Info.get("isType2NdefCandidate"):
			signals.append({
				"id": "cheap_tag_family_candidate",
				"severity": "low",
				"why": "Many Low-Cost Systems Rely On Simple Tag Families With Weak Or No Auth",
			})
			score += 1

		# Keep Score Bounded (0..10)
		score = max(0, min(10, score))
		return signals, score
