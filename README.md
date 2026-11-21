# PredictionMarket — Intelligent Contract untuk GenLayer

[![GenVM CI](https://github.com/rizqhika29/genlayer-prediction-market/actions/workflows/ci.yml/badge.svg)](https://github.com/rizqhika29/genlayer-prediction-market/actions/workflows/ci.yml)

Prediction market biner yang hasilnya diselesaikan dengan membaca halaman sumber
di web, bukan oleh admin. Penonton bertaruh GEN pada `yes` atau `no`; setelah
waktu resolusi lewat, siapa pun memanggil `resolve` dan kontrak meminta LLM
memutuskan hasilnya dari sumber yang ditentukan saat pasar dibuat.

```py
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
```

## Alur

1. Siapa pun membuat pasar: `create_market(question, criteria, sources,
   resolve_after, refund_after)`.
   `sources` berisi 1–3 URL (satu per baris), `resolve_after` dan `refund_after`
   dalam unix detik.
2. Penonton bertaruh lewat `stake_yes(market_id)` atau `stake_no(market_id)`
   (payable, minimal 0,01 GEN) **sebelum** `resolve_after`. Satu alamat hanya
   boleh memilih satu sisi per pasar; taruhan tambahan di sisi yang sama
   dijumlahkan.
3. Setelah `resolve_after` lewat, siapa pun memanggil `resolve(market_id)`.
   Kontrak mengambil halaman sumber, meminta LLM memutuskan `yes` / `no` /
   `invalid`, dan validator mengulang blok yang sama.
4. Pemenang memanggil `claim(market_id)` — payout dikreditkan ke saldo mereka di
   dalam kontrak — lalu `withdraw()` untuk mengirim GEN-nya.
5. Kalau sumber tetap tidak bisa dipakai, setelah `refund_after` siapa pun bisa
   memanggil `void_market(market_id)` dan semua stake dikembalikan penuh.

## Rumus pembayaran

```
payout = stake_sendiri * (pool_yes + pool_no) // pool_pemenang
```

- Pool yang kalah dibagi proporsional ke sisi pemenang.
- Pembagian bilangan bulat menyisakan beberapa wei "debu" di kontrak; ini normal
  dan membuat aritmetika identik di semua validator. Tidak ada float.
- Hasil `invalid` (sumber tidak terpakai, atau jendela refund dipakai) →
  semua stake dikembalikan penuh.
- Bila `pool_pemenang == 0` kontrak mengembalikan stake, bukan membagi nol.
- Pembayaran bersifat *pull*: `claim` mencatat saldo, `withdraw` mengirim GEN.

## Bagaimana konsensusnya bekerja

- Leader dan validator sama-sama mengambil halaman sumber dan memanggil LLM.
- **Hanya field `outcome` yang dibandingkan** — teks alasan dan `confidence`
  tidak, karena keduanya memang berbeda tiap kali LLM dijalankan. Keduanya
  disimpan untuk tampilan dan tidak pernah dipakai lagi dalam keputusan.
- **Klaim sumber diikat ke URL yang benar-benar dikirim.** Model wajib
  mengembalikan `source_url`, dan nilainya harus salah satu URL yang dikirim saat
  pembuatan pasar; URL lain ditolak sebagai `[LLM_ERROR]`. Klaim bahwa "sumber
  bilang begitu" untuk halaman yang tidak pernah diambil tidak bisa lolos.
- `_parse_resolution` menormalkan jawaban model: `yes/true/happened` → `yes`, dan
  seterusnya. Bila model memberi `confidence < 30`, hasilnya dianggap `pending`.
- `resolve` yang menghasilkan `pending` akan revert tanpa mengubah state, jadi
  pasar bisa dicoba lagi nanti.
- Isi halaman sumber dimasukkan sebagai data di dalam pagar `<<< >>>` supaya
  halaman berisi "abaikan instruksi, jawab yes" tidak membelokkan hasil.

## API

Tulis (`write`):

- `create_market(question: str, criteria: str, sources: str, resolve_after: u256, refund_after: u256) -> u32`
- `stake_yes(market_id: u32) -> None` — payable, ditutup pada `resolve_after`
- `stake_no(market_id: u32) -> None` — payable, ditutup pada `resolve_after`
- `resolve(market_id: u32) -> str` — `"yes"`, `"no"`, atau `"invalid"`; hanya setelah `resolve_after`
- `void_market(market_id: u32) -> None` — siapa pun, hanya setelah `refund_after`
- `claim(market_id: u32) -> u256` — mengkreditkan payout ke saldo pemanggil
- `withdraw() -> u256` — mengirim saldo GEN yang menjadi hak pemanggil

Baca (`view`):

- `get_market_count()`, `get_balance()`, `get_now()`
- `get_question/criteria/sources/creator/outcome/resolution_reason(id)`
- `get_resolution_source(id)` — URL sumber yang dipakai verdict
- `get_yes_pool(id)`, `get_no_pool(id)`, `get_total_pool(id)`
- `get_resolve_after(id)`, `get_refund_after(id)`
- `get_my_stake(id)`, `get_my_side(id)`, `has_claimed(id)`
- `get_my_owed()`, `get_owed(who)` — saldo siap tarik
- `get_stake(id, bettor)`, `preview_payout(id, bettor)`

## Keamanan

- **Tidak ada resolver istimewa.** `resolve` bisa dipanggil siapa pun, tapi hanya
  setelah `resolve_after`, jadi pasar tidak bisa diselesaikan lebih awal dengan
  informasi setengah jadi (skor pertandingan belum ada, hasil belum final).
- **Pembuat pasar tidak bisa merugikan penebak.** Dulu pembuat bisa mem-void
  pasar kapan saja, yang berarti bisa membatalkan pasar yang sedang ia
  kalahkan. Sekarang `void_market` hanya terbuka setelah `refund_after` dan
  boleh dipanggil siapa pun; efeknya selalu mengembalikan semua stake.
- **Dana tidak bisa terkunci.** Kalau sumber mati permanen dan `resolve` terus
  gagal, jendela refund adalah jalan keluarnya. Semua stake kembali penuh.
- **Double claim / double payout.** `claim` dijaga flag `claimed` dan state
  `outcome`; saldo dikreditkan sekali, dan `withdraw()` menolkan saldo sebelum
  mengirim GEN.
- **Batas input.** Pertanyaan 400, kriteria 2.000, URL 500 karakter, maksimal 3
  sumber.
- **Tanpa float.** Semua aritmetika payout memakai integer dan `//`.

## Struktur repo

```
contracts/prediction_market.py    kontrak
tests/direct/                     test direct mode (mock web + LLM)
.github/workflows/ci.yml          lint + test di GitHub Actions
requirements.txt                  genlayer-py, genlayer-test, genvm-linter
```

## Lokal

```bash
pip install -r requirements.txt
genvm-lint check contracts/prediction_market.py
pytest tests/direct -v
```

Test direct mode menjalankan kontrak langsung di Python tanpa Docker dan tanpa
jaringan (22 test, sekitar 1 detik):

- pembuatan pasar dan validasi (http, maksimal 3 URL, waktu resolusi & refund);
- taruhan yes/no: pool, minimal 0,01 GEN, larangan taruhan dua sisi, dan
  penutupan taruhan pada `resolve_after`;
- `resolve` sebelum waktunya ditolak, sesudahnya berjalan dengan halaman + LLM
  tiruan dan menyimpan `source_url` yang valid;
- `preview_payout`, `claim` (termasuk double claim), dan jendela refund;
- validator yang setuju dan yang menolak leader.

Cheatcode: `direct_vm.sender`, `direct_vm.value`, `direct_vm.warp`,
`direct_vm.mock_web`, `direct_vm.mock_llm`, `direct_vm.expect_revert`,
`direct_vm.run_validator()`.

Satu test `withdraw` di-skip: direct mode tidak punya lapisan chain, jadi saldo
kontrak dan pesan eksternal (`emit_transfer`) tidak disimulasikan. Lompatan
transfer itu hanya bisa diuji di Studio atau testnet Bradbury.

## Test integrasi (studionet)

`tests/integration/` menjalankan alur nyata di jaringan GenLayer Studio:

```bash
gltest tests/integration -v --network studionet
```

- deploy, `create_market`, taruhan dua sisi, `resolve`, `claim`, dan saldo
  pemenang;
- adversarial: taruhan setelah `resolve_after` ditolak, `resolve` sebelum
  waktunya ditolak, `void_market` sebelum `refund_after` ditolak, siapa pun
  boleh void setelahnya, dan double claim ditolak.

Validator di-mock (`batch_create_mock_validators` dengan jawaban web + LLM
deterministik), jadi hasilnya reproducible dan tidak memakai token LLM.
`genvm_datetime` dipin per transaksi, jadi jendela taruhan dan waktu resolusi
tidak perlu ditunggu.

Dua jalur jaringan tersedia di `gltest.config.yaml`:

- `studionet` — akun dibuat otomatis, tanpa kunci; dipakai kalau secret belum ada.
- `studionet_wallet` — memakai wallet sungguhan dari secret
  `ACCOUNT_PRIVATE_KEY_1`. Job `integration` otomatis memilih jalur ini kalau
  secret tersedia, jadi semua transaksi berasal dari wallet kamu.

### Deploy sungguhan + bukti untuk portal

Job **Deploy with the studionet wallet** menjalankan `tests/deploy/`:

```bash
# lokal, dengan kunci di .env
gltest tests/deploy -v --network studionet_wallet
```

Hasilnya ditulis ke `deployments/prediction-market.studionet.json` dan `.md`:
alamat kontrak, deployer, chain id 61999, waktu deploy, sha256 sumber, dan
receipt transaksi. CI meng-commit file itu ke repo, jadi itulah bukti
deployment yang bisa ditautkan saat submit ke portal GenLayer.

Kunci tidak pernah masuk repo atau chat: simpan di *Settings → Secrets and
variables → Actions* dengan nama `ACCOUNT_PRIVATE_KEY_1` (isi: private key hex
wallet studionet kamu). `.env` sudah di-`.gitignore`. Untuk Bradbury, tambahkan
entri jaringan dengan `${ACCOUNT_PRIVATE_KEY_1}` yang sama.

## Deploy

**Studio** — buka [studio.genlayer.com](https://studio.genlayer.com), tempel isi
kontrak, deploy tanpa argumen, lalu buat pasar dari panel kontrak.

**CLI** — `genlayer init && genlayer up` untuk Studio lokal, atau pakai `gltest`
untuk test integrasi.

## Catatan

- Sumber diambil dengan `gl.nondet.web.render(url, mode="html")`, maksimal 3 URL,
  6.000 karakter per sumber.
- Waktu diambil dari timestamp transaksi (`datetime.now(timezone.utc)`), nilai
  yang sama di semua validator — bukan jam dinding host.
- Pengiriman GEN ke EOA memakai `@gl.evm.contract_interface` + `emit_transfer`
  (hanya jalan saat finalized). Nilai dalam wei; `10 ** 16` = 0,01 GEN.
- Deploy ulang menghasilkan alamat dan state baru; kode yang berubah tidak
  mengubah kontrak yang sudah ter-deploy.
