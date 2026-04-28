#!/usr/bin/env python3
# Type 2 Tag Tools (NTAG213/NTAG215/NTAG216 And Other NFC Forum Type 2 Tags).
# Pages Are 4 Bytes Each. Page 0..2 Hold UID/BCC/Internal/Static Lock Bytes.
# Page 3 Is The Capability Container. User Memory Begins At Page 4.
# This Module Reuses The Underlying PN532Module's Driver Handle And Lock For Thread Safety.

import time
from typing import Any, Dict, List, Optional

import ndef


# CC[2] Is Memory Size In 8-Byte Units. Map Known NTAG Variants For Friendly Naming
# And Authoritative Page Counts. Anything Else Falls Back To Probe-Based Sizing.
NTAG_BY_CC = {
	0x12: {
		"product": "NTAG213",
		"userBytes": 144,
		"firstUserPage": 4,
		"lastUserPage": 39,
		"firstConfigPage": 40,
		"lastConfigPage": 44,
	},
	0x3E: {
		"product": "NTAG215",
		"userBytes": 504,
		"firstUserPage": 4,
		"lastUserPage": 129,
		"firstConfigPage": 130,
		"lastConfigPage": 134,
	},
	0x6D: {
		"product": "NTAG216",
		"userBytes": 888,
		"firstUserPage": 4,
		"lastUserPage": 225,
		"firstConfigPage": 226,
		"lastConfigPage": 230,
	},
}

# Hard Cap For Probe Reads So We Never Loop Forever On A Misbehaving Tag.
PROBE_MAX_PAGE = 240


class Type2TagTools:
	"""
	NFC Forum Type 2 Helpers For NTAG213/NTAG215/NTAG216 And Compatible Tags.
	Holds A Reference To The Existing PN532Module So Driver State And The
	Underlying threading.Lock Stay Coordinated With The Other NFC Operations.
	"""

	def __init__(self, pn532Module):
		# Delegate To The Same PN532Module Instance So We Share Its Driver And Lock
		self._mod = pn532Module

	# ----------------------------
	# Internal Helpers
	# ----------------------------
	def _driver(self):
		# Resolve The Underlying adafruit_pn532 Driver Handle Lazily
		return getattr(self._mod, "_pn532", None)

	def _bytesToHex(self, b: Optional[bytes]) -> Optional[str]:
		if b is None:
			return None
		return "".join([f"{x:02x}" for x in b])

	def _selectTag(self, timeoutSeconds: float = 0.5) -> Optional[bytes]:
		# Wakes Up A Type A Tag In The Field And Returns Its UID Bytes Or None
		drv = self._driver()
		if drv is None:
			return None
		try:
			return drv.read_passive_target(timeout=timeoutSeconds)
		except Exception:
			return None

	def _readPage(self, page: int) -> Optional[bytes]:
		drv = self._driver()
		if drv is None or not hasattr(drv, "ntag2xx_read_block"):
			return None
		try:
			return drv.ntag2xx_read_block(page)
		except Exception:
			return None

	def _writePage(self, page: int, data: bytes) -> bool:
		drv = self._driver()
		if drv is None or not hasattr(drv, "ntag2xx_write_block"):
			return False
		try:
			return bool(drv.ntag2xx_write_block(page, bytes(data)))
		except Exception:
			return False

	# ----------------------------
	# Capability Container Parsing
	# ----------------------------
	def parseType2Cc(self, page3: Optional[bytes]) -> Dict[str, Any]:
		"""
		Decode The Type 2 Capability Container From Page 3.
		Returns A Stable Dict Even When The CC Is Missing/Invalid.
		"""
		out: Dict[str, Any] = {
			"ccHex": self._bytesToHex(page3),
			"isType2Ndef": False,
			"magic": None,
			"version": None,
			"sizeUnit": None,
			"dataBytes": None,
			"access": None,
			"product": None,
		}
		if not page3 or len(page3) < 4:
			return out

		out["magic"] = page3[0]
		out["version"] = page3[1]
		out["sizeUnit"] = page3[2]
		out["access"] = page3[3]
		out["isType2Ndef"] = (page3[0] == 0xE1)
		out["dataBytes"] = int(page3[2]) * 8

		variant = NTAG_BY_CC.get(int(page3[2]))
		if variant:
			out["product"] = variant["product"]
		return out

	# ----------------------------
	# Sizing / Page Count Probe
	# ----------------------------
	def probeType2PageCount(self, startPage: int = 4) -> Dict[str, Any]:
		"""
		Walk Forward From startPage Until A Read Fails. Returns The Highest
		Successfully Read Page Plus A Total Page Count Estimate. Bounded By
		PROBE_MAX_PAGE So A Misbehaving Tag Cannot Loop Forever.
		"""
		highest = -1
		for page in range(startPage, PROBE_MAX_PAGE + 1):
			block = self._readPage(page)
			if block is None:
				break
			highest = page
		totalPages = highest + 1 if highest >= 0 else 0
		return {
			"ok": True,
			"highestReadablePage": highest,
			"totalPagesEstimate": totalPages,
		}

	# ----------------------------
	# Detection / Basic Info
	# ----------------------------
	def detectType2Tag(self, timeoutSeconds: float = 0.6) -> Dict[str, Any]:
		"""
		Determine Whether A Tag In The Field Looks Like An NFC Forum Type 2 Tag.
		Reads Pages 0..3, Parses CC, And Reports A Friendly Product Guess.
		Does Not Run GET_VERSION Because The adafruit_pn532 Driver Does Not
		Expose It Publicly. Falls Back To CC[2] / Probe-Based Sizing.
		"""
		with self._mod._lock:
			drv = self._driver()
			if drv is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			uid = self._selectTag(timeoutSeconds=timeoutSeconds)
			if uid is None:
				return {"ok": True, "found": False}

			pages: Dict[int, Optional[str]] = {}
			pageBytes: Dict[int, bytes] = {}
			for p in range(0, 4):
				blk = self._readPage(p)
				pages[p] = self._bytesToHex(blk)
				if blk is not None:
					pageBytes[p] = blk

			page3 = pageBytes.get(3)
			cc = self.parseType2Cc(page3)
			isType2 = bool(cc["isType2Ndef"])

			# Static Lock Bytes Live In Page 2 Bytes 2..3 For Most NTAG/Ultralight
			lockBytes = None
			likelyLocked = None
			page2 = pageBytes.get(2)
			if page2 and len(page2) >= 4:
				lockBytes = [int(page2[2]), int(page2[3])]
				likelyLocked = any(b != 0x00 for b in lockBytes)

			# Empty NDEF Heuristic: First User Page Is 03 00 FE .. Or All Zero
			emptyNdef = None
			if isType2:
				userPage = self._readPage(4)
				if userPage is not None:
					if userPage[0] == 0x03 and userPage[1] == 0x00:
						emptyNdef = True
					elif all(b == 0x00 for b in userPage):
						emptyNdef = True
					else:
						emptyNdef = False

			return {
				"ok": True,
				"found": True,
				"uidHex": self._bytesToHex(uid),
				"uidLen": len(uid),
				"isType2Ndef": isType2,
				"product": cc["product"],
				"cc": cc,
				"pages": pages,
				"staticLockBytes": lockBytes,
				"likelyLocked": likelyLocked,
				"emptyNdef": emptyNdef,
				"note": (
					"Detected NFC Forum Type 2 Tag" if isType2
					else "Tag Selected But CC Page 3 Did Not Start With 0xE1; May Not Be A Type 2 NDEF Tag."
				),
			}

	def getType2BasicInfo(self) -> Dict[str, Any]:
		# Convenience Alias So Callers Can Pick Whichever Name They Like
		return self.detectType2Tag()

	def readType2Page(self, page: int) -> Dict[str, Any]:
		# Single-Page Read Helper. Mostly For Future Callers / Debugging.
		if not isinstance(page, int) or page < 0 or page > PROBE_MAX_PAGE:
			return {"ok": False, "error": "Invalid Page Number"}
		with self._mod._lock:
			if self._driver() is None:
				return {"ok": False, "error": "PN532 Not Initialized"}
			if self._selectTag(0.5) is None:
				return {"ok": True, "found": False}
			blk = self._readPage(page)
			if blk is None:
				return {"ok": False, "error": f"Read Failed At Page {page}"}
			return {"ok": True, "page": page, "hex": self._bytesToHex(blk)}

	# ----------------------------
	# Dump
	# ----------------------------
	def dumpType2Tag(self) -> Dict[str, Any]:
		"""
		Read Pages Starting At 0 Until A Detected Variant's Page Count Is
		Reached, Or Until A Read Fails. Annotates Each Page With Its Region
		(Manufacturer / Lock / CC / User / Config) Where Known.
		"""
		with self._mod._lock:
			drv = self._driver()
			if drv is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			uid = self._selectTag(timeoutSeconds=0.6)
			if uid is None:
				return {"ok": True, "found": False}

			# CC First So We Can Pick A Sensible Stop Page
			page3 = self._readPage(3)
			cc = self.parseType2Cc(page3)
			variant = NTAG_BY_CC.get(int(page3[2])) if (page3 and len(page3) >= 4) else None

			if variant:
				stopPage = variant["lastConfigPage"]
				detectedBy = "cc"
			else:
				probe = self.probeType2PageCount(startPage=4)
				highest = probe.get("highestReadablePage", -1)
				stopPage = highest if highest >= 0 else 3
				detectedBy = "probe"

			pages: List[Dict[str, Any]] = []
			lastReadable = -1
			for p in range(0, stopPage + 1):
				blk = self._readPage(p)
				if blk is None:
					# Stop Early If Reads Start Failing — Tag May Be Smaller Than Expected
					break
				lastReadable = p

				region = self._classifyPageRegion(p, variant)
				pages.append({
					"page": p,
					"hex": self._bytesToHex(blk),
					"region": region,
				})

			return {
				"ok": True,
				"found": True,
				"uidHex": self._bytesToHex(uid),
				"product": cc["product"],
				"cc": cc,
				"detectedBy": detectedBy,
				"pageSizeBytes": 4,
				"pageCount": len(pages),
				"highestReadablePage": lastReadable,
				"pages": pages,
			}

	def _classifyPageRegion(self, page: int, variant: Optional[Dict[str, Any]]) -> str:
		# Friendly Region Label For UI Annotation. Variant Is Authoritative When Known.
		if page in (0, 1):
			return "manufacturer"
		if page == 2:
			return "lock/internal"
		if page == 3:
			return "cc"
		if variant:
			if variant["firstUserPage"] <= page <= variant["lastUserPage"]:
				return "user"
			if variant["firstConfigPage"] <= page <= variant["lastConfigPage"]:
				return "config"
			return "unknown"
		return "user"

	# ----------------------------
	# NDEF Read (Type 2 Only)
	# ----------------------------
	def readType2Ndef(self) -> Dict[str, Any]:
		"""
		Parse An NDEF TLV From Type 2 User Memory Starting At Page 4.
		Reuses The PN532Module's _classicFindNdefTlv Helper So Decoding
		Stays Consistent With The Rest Of The NFC Page.
		"""
		with self._mod._lock:
			drv = self._driver()
			if drv is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			uid = self._selectTag(timeoutSeconds=1.0)
			if uid is None:
				return {"ok": True, "found": False, "hasNdef": False}

			# Validate It Is A Type 2 NDEF Tag Before Spending Time On Reads
			page3 = self._readPage(3)
			cc = self.parseType2Cc(page3)
			if not cc["isType2Ndef"]:
				return {
					"ok": True,
					"found": True,
					"hasNdef": False,
					"uidHex": self._bytesToHex(uid),
					"note": "Not A Type 2 NDEF Tag (CC Page 3 Does Not Start With 0xE1).",
				}

			# Bound The Read By CC[2] If Available, Else Probe Modestly
			variant = NTAG_BY_CC.get(int(page3[2])) if page3 else None
			if variant:
				lastUser = variant["lastUserPage"]
			else:
				dataBytes = cc.get("dataBytes") or 0
				if dataBytes > 0:
					lastUser = 4 + (dataBytes // 4) - 1
				else:
					lastUser = 4 + 64

			data = bytearray()
			for p in range(4, lastUser + 1):
				blk = self._readPage(p)
				if blk is None:
					break
				data.extend(blk)

			ndefStart, ndefLen = self._mod._classicFindNdefTlv(bytes(data))
			if ndefStart is None or ndefLen is None:
				return {
					"ok": True,
					"found": True,
					"hasNdef": False,
					"uidHex": self._bytesToHex(uid),
					"product": cc["product"],
				}

			rawMsg = bytes(data[ndefStart : ndefStart + ndefLen])
			records = self._decodeNdefRecords(rawMsg)
			return {
				"ok": True,
				"found": True,
				"hasNdef": True,
				"uidHex": self._bytesToHex(uid),
				"product": cc["product"],
				"tagFamily": "Type 2 (NTAG/Ultralight-Like)",
				"ndefHex": self._bytesToHex(rawMsg),
				"records": records,
			}

	def _decodeNdefRecords(self, rawMsg: bytes) -> List[Dict[str, Any]]:
		# Mirrors The Decoder Branch In PN532Module.tryReadNdef For Consistency
		out: List[Dict[str, Any]] = []
		try:
			for rec in ndef.message_decoder(rawMsg):
				if isinstance(rec, ndef.TextRecord):
					out.append({"type": "text", "text": rec.text, "lang": rec.language})
				elif isinstance(rec, ndef.UriRecord):
					out.append({"type": "uri", "uri": rec.uri})
				else:
					out.append({
						"type": "other",
						"tnf": int(getattr(rec, "tnf", -1)),
						"recordType": (
							rec.type.decode("utf-8", "ignore")
							if isinstance(getattr(rec, "type", b""), (bytes, bytearray))
							else str(getattr(rec, "type", ""))
						),
						"payloadHex": (
							rec.payload.hex()
							if isinstance(getattr(rec, "payload", b""), (bytes, bytearray))
							else str(getattr(rec, "payload", ""))
						),
					})
		except Exception as e:
			out.append({"type": "decodeError", "error": str(e)})
		return out

	# ----------------------------
	# Wipe User Memory
	# ----------------------------
	def wipeType2UserMemory(self, mode: str = "ndef") -> Dict[str, Any]:
		"""
		Safely Clear Type 2 User Data. Never Touches Pages 0..3 Or Config/Lock
		Pages. Two Modes:
		  - "ndef": Write An Empty NDEF TLV (03 00 FE 00) At Page 4 Only. Fast,
		            Low-Risk, And Leaves The Tag Cleanly Formatted.
		  - "user": Zero Every User Page Between firstUserPage..lastUserPage.
		           Useful When The Tag Has Stale Data Past Page 4.
		Reads Back Page 4 To Verify Before Returning.
		"""
		mode = (mode or "ndef").strip().lower()
		if mode not in ("ndef", "user"):
			return {"ok": False, "error": "Invalid Mode (Expected 'ndef' Or 'user')"}

		with self._mod._lock:
			drv = self._driver()
			if drv is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			uid = self._selectTag(timeoutSeconds=0.6)
			if uid is None:
				return {"ok": True, "found": False}

			page3 = self._readPage(3)
			cc = self.parseType2Cc(page3)
			if not cc["isType2Ndef"]:
				return {
					"ok": False,
					"error": "Not A Type 2 NDEF Tag (CC Page 3 Does Not Start With 0xE1). Refusing To Wipe.",
					"uidHex": self._bytesToHex(uid),
				}

			variant = NTAG_BY_CC.get(int(page3[2])) if page3 else None
			firstUserPage = variant["firstUserPage"] if variant else 4
			lastUserPage = variant["lastUserPage"] if variant else None

			pagesWritten: List[int] = []
			pagesFailed: List[int] = []

			if mode == "ndef":
				# Empty NDEF TLV: 03 00 FE 00. Single Page Write At The Start Of User Memory.
				ok = self._writePage(firstUserPage, bytes([0x03, 0x00, 0xFE, 0x00]))
				if ok:
					pagesWritten.append(firstUserPage)
				else:
					pagesFailed.append(firstUserPage)
			else:
				# Zero Every User Page. Stop If A Write Fails (Likely Locked Or End Of Memory)
				if lastUserPage is None:
					# Probe-Based Bound. Be Conservative: Stop On First Read Failure Past firstUserPage.
					probe = self.probeType2PageCount(startPage=firstUserPage)
					highest = probe.get("highestReadablePage", -1)
					lastUserPage = highest if highest >= 0 else firstUserPage

				zeroPage = bytes([0x00, 0x00, 0x00, 0x00])
				for p in range(firstUserPage, lastUserPage + 1):
					if self._writePage(p, zeroPage):
						pagesWritten.append(p)
					else:
						pagesFailed.append(p)
						break

			# Verify By Reading Back Page 4
			verifyHex = self._bytesToHex(self._readPage(firstUserPage))

			return {
				"ok": len(pagesWritten) > 0 and not pagesFailed,
				"found": True,
				"uidHex": self._bytesToHex(uid),
				"mode": mode,
				"product": cc["product"],
				"firstUserPage": firstUserPage,
				"lastUserPage": lastUserPage,
				"pagesWritten": pagesWritten,
				"pagesFailed": pagesFailed,
				"verifyPage4Hex": verifyHex,
				"note": (
					"Wrote Empty NDEF TLV To Page 4." if mode == "ndef"
					else f"Zeroed {len(pagesWritten)} User Pages."
				),
			}

	# ----------------------------
	# Format Empty NDEF
	# ----------------------------
	def formatEmptyType2Ndef(self) -> Dict[str, Any]:
		"""
		Ensure The Tag Has A Valid Type 2 CC At Page 3 And An Empty NDEF TLV
		At Page 4. If CC Is Already Valid, Page 3 Is Left Alone. Never Writes
		Page 0/1/2 Or Lock/Config Pages.
		"""
		with self._mod._lock:
			drv = self._driver()
			if drv is None:
				return {"ok": False, "error": "PN532 Not Initialized"}

			uid = self._selectTag(timeoutSeconds=0.6)
			if uid is None:
				return {"ok": True, "found": False}

			page3 = self._readPage(3)
			cc = self.parseType2Cc(page3)

			ccWritten = False
			ccChosen = None

			if not cc["isType2Ndef"]:
				# Probe Page Count To Pick A Plausible CC[2]. Be Conservative: Default To
				# NTAG213 Layout If We Cannot Tell. We Refuse To Guess A Larger Tag Than
				# We Can Verify, So We Never Advertise More Memory Than Exists.
				probe = self.probeType2PageCount(startPage=4)
				highest = probe.get("highestReadablePage", -1)
				if highest >= 225:
					ccBytes = bytes([0xE1, 0x10, 0x6D, 0x00])
				elif highest >= 129:
					ccBytes = bytes([0xE1, 0x10, 0x3E, 0x00])
				else:
					ccBytes = bytes([0xE1, 0x10, 0x12, 0x00])

				if not self._writePage(3, ccBytes):
					return {
						"ok": False,
						"error": "CC Write Failed At Page 3. Tag May Be Locked Or Not A Type 2 Tag.",
						"uidHex": self._bytesToHex(uid),
					}
				ccWritten = True
				ccChosen = self._bytesToHex(ccBytes)
				# Re-Parse After Writing
				page3 = self._readPage(3)
				cc = self.parseType2Cc(page3)

			variant = NTAG_BY_CC.get(int(page3[2])) if page3 else None
			firstUserPage = variant["firstUserPage"] if variant else 4

			ok = self._writePage(firstUserPage, bytes([0x03, 0x00, 0xFE, 0x00]))
			if not ok:
				return {
					"ok": False,
					"error": f"Empty NDEF TLV Write Failed At Page {firstUserPage}.",
					"uidHex": self._bytesToHex(uid),
					"ccWritten": ccWritten,
					"ccChosen": ccChosen,
				}

			verifyHex = self._bytesToHex(self._readPage(firstUserPage))
			return {
				"ok": True,
				"found": True,
				"uidHex": self._bytesToHex(uid),
				"product": cc["product"],
				"ccWritten": ccWritten,
				"ccChosen": ccChosen,
				"firstUserPage": firstUserPage,
				"verifyPage4Hex": verifyHex,
				"note": "Tag Now Has Valid CC And Empty NDEF TLV.",
			}

	# ----------------------------
	# TLV Helper (Re-Exposed)
	# ----------------------------
	def extractNdefTlv(self, data: bytes):
		# Convenience Wrapper Around The PN532Module's TLV Scanner
		return self._mod._classicFindNdefTlv(bytes(data))
