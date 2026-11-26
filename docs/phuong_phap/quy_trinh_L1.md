# Quy trình vận hành mô hình L1 (phi tài chính và ngân hàng)

| | |
|---|---|
| Phiên bản mô hình | Thư viện **v2.6.6** (2026-11-26): L1 phi tài chính chốt v2.6.6 (lãi suất vay quý gần nhất); ngân hàng: chi phí tín dụng theo quý |
| Người đọc | Người vận hành, bảo trì mô hình; team IT (đóng gói thư viện/API) |
| Tài liệu gốc | Phương pháp: `docs/L1_phi_tai_chinh.md`, `docs/thiet_ke_bank_L1.md`; kiến trúc code: `docs/kien_truc_code.md` |
| Báo cáo kèm theo | `docs/report/Bao_cao_mo_hinh_L1_v2.6.3.pptx`; backtest: `docs/backtest/backtest_report_v2.6.3.html` |

Tài liệu này mô tả **cách chạy, kiểm tra và thay đổi** mô hình. Công thức, default và tham số chi tiết nằm trong spec;
khi spec và tài liệu này khác nhau thì spec đúng.

---

## 1. Mục đích, phạm vi, giới hạn

- **Mục đích.** Dự phóng nhanh BCTC (KQKD, CĐKT, LCTT) cho mã chưa có analyst theo dõi, chạy tự động, không cần nhập
  tay. Với mã có analyst, L1 là mức sàn của mọi block.
- **Phạm vi.** Nhóm BCTC phi tài chính (mọi template ngành kế thừa: sản xuất, bán lẻ, xây dựng, …), **không gồm bất động
  sản**; nhóm ngân hàng. Bất động sản, chứng khoán, bảo hiểm sẽ có engine riêng.
- **Không phải** khuyến nghị đầu tư, định giá hay mô hình chi tiết thay analyst.
- **Mức sai số kỳ vọng** (backtest v2.6.3, sai số trung vị, dự phóng cả năm từ đầu năm): năm bình thường 2024 – nay,
  doanh thu ~11%, LNST công ty mẹ ~31%; ngân hàng LNST ~16%. Năm chu kỳ đổi chiều (2022–2023) LNST lệch 35–52%.
- **Cộng cả nhóm, cả ngành sát hơn từng DN** (sai lệch các DN bù trừ): tổng LNST cả nhóm phi tài chính lệch ~22% (cả
  năm) và ~15% (quý tới), so với ~31% và ~40% của một DN điển hình (2024 – nay). Ngoại lệ: ngành ít DN mà một DN chi
  phối (bán lẻ, 6 DN).
- **Sai số lớn tập trung ở cú đảo chiều 2022**: tính trên riêng các quý còn phải dự phóng, tổng LNST cả nhóm lệch 35–40%
  sau Q1–Q3 với mẫu 2022 – nay, nhưng chỉ 19%, 9%, 3% với mẫu 2023 – nay.

## 2. Dữ liệu

| Nội dung | Quy ước |
|---|---|
| Nguồn | BigQuery, **chỉ đọc**, qua lớp adapter; danh tính qua ADC/biến môi trường, không lưu khóa trong repo. Bản phát triển dùng file thô đã tải (`data/raw/<mã>`, manifest ở `sql/<mã>/`) |
| Mapping | `mapping/GBQ_fields.csv` / `.json` (schema), `mapping/mapping_fiin_all.xlsx` (nghĩa mã BCTC), `mapping/FiinX_Ratio_Mapping.xlsx` (tỷ lệ). Không suy nghĩa khoản mục từ số hiệu mã ISA/BSA/CFA |
| Phạm vi BCTC | Hợp nhất; không trộn với riêng lẻ, không trùng phiên bản báo cáo |
| Kỳ | Báo cáo năm (FY) + quý đứng riêng. Dòng IS/CF lũy kế được chuyển về quý đứng riêng; số dư BS lấy tại thời điểm, **không lấy hiệu số dư** |
| Kiểm tra lịch sử | Đẳng thức BCTC, tiền LCTT khớp tiền BS, cộng 4 quý so với năm (chênh lệch ghi mức `info`, cho qua), liên tục số dư tiền, trùng dòng, độ phủ (`quick_model/data/checks.py`) |
| Hạn chế | Lịch sử không point-in-time (số đã điều chỉnh hồi tố) |

Lịch sử thật không bị sửa để cân. Ô trống khác 0.

## 3. Quy trình dự phóng một mã

| Bước | Việc | Code | Đầu ra |
|---|---|---|---|
| 1 | Chọn hồ sơ công ty: mã → template → thư mục dữ liệu | `config/companies.json`, `quick_model/companies.py` | Template (nhóm BCTC, ngành) |
| 2 | Nạp lịch sử, kiểm tra | `quick_model/data/history.py` (`load_history`), `data/checks.py` | `History` + danh sách cảnh báo dữ liệu |
| 3 | Xác định năm gốc = báo cáo FY mới nhất có trong dữ liệu | `engine/forecast.py` (`default_origin`) | Nhãn năm gốc |
| 4 | Tạo kịch bản | `new_scenario` | `Scenario` (template, năm gốc, chân trời) |
| 5 | Làm sạch khoản bất thường (tự áp, công bố từng khoản) | `quick_model/oneoff.py` | Chuỗi lịch sử đã xử lý; mã ONE-02/ONE-03 |
| 6 | Tính default mọi giả định | `engine/defaults.py` (`compute_defaults`, `defaults_for_scenario`) | Giá trị + nguồn (`historical_default`, `explicit_policy`, `needs_input`) |
| 7 | Chạy engine quý + năm | `quick_model/hybrid.py`, `engine/forecast.py` (`run_forecast`) | Ba báo cáo theo quý đến hết Q4 năm sau, rồi theo năm |
| 8 | Cân nguồn (chỉ khi nợ vay ở L1) | engine (vay tự động), `families/nonfinancial/balancing.py` | Vay tự động, funding gap công bố |
| 9 | Kiểm tra, gắn trạng thái | `families/nonfinancial/engine.py` (kiểm tra mỗi kỳ, trạng thái) | Lỗi chặn / cảnh báo / thông tin; nhãn “có thể công bố” |
| 10 | Giải thích ô, xuất hồ sơ | `quick_model/explain.py`, `tools/export_model_doc.py` | Trace từng ô; `docs/models/<MÃ>/...json/xlsx/docx` |

Ví dụ xuất hồ sơ một mã:

```bash
.venv/Scripts/python tools/export_model_doc.py --ticker HPG --freq Y
```

Ghi duyệt (`--approval da_duyet --reviewer ... --approved-at ...`) **chỉ khi owner yêu cầu**.

**Engine quý + năm (tóm tắt).** Vòng tính theo quý từ quý sau quý công bố gần nhất đến hết Q4 năm sau, rồi theo năm.
Năm có quý = tổng các quý (số dư lấy Q4), nên xem theo quý hay năm đều cùng con số. Ràng buộc (tiền tối thiểu, trần nợ;
ngân hàng: trần LDR, sàn/trần liên ngân hàng) chỉ kiểm ở cuối năm. Analyst ghi đè số năm = mục tiêu năm, phần chênh dồn
vào các quý còn lại (2 vòng giải cố định, không lặp đến hội tụ).

## 4. Default giả định (tóm tắt)

Chi tiết từng giả định: spec mục 5. Các quy tắc then chốt:

| Nhóm | Năm | Quý |
|---|---|---|
| Tăng trưởng doanh thu | Hội tụ từ YoY gần nhất về min(EMA 5 năm, 8%) trong 5 năm | 50% QoQ mùa vụ + 15% YoY + 35% năm, trên nền điều chỉnh mức 2 quý; mùa vụ co α = 0,6; trần 1,5 × bình quân 4 quý |
| Tỷ lệ trên doanh thu/giá vốn (biên, CPBH/CPQL biến đổi, số ngày VLĐ) | Hội tụ về EMA 5 năm trong 4 năm | 0,65 × EMA 8 quý (tử số / mẫu số) + 0,35 × năm |
| Biên gộp quý | — | TTM + 0,25 × quán tính quý gần nhất theo trọng số doanh thu |
| DT tài chính, LDLK | Mức năm gần nhất (đã làm sạch) | Bình quân 4 quý quy năm |
| Lãi suất vay | Lãi vay / dư nợ bình quân (đầu + cuối kỳ) kỳ liền trước | Như năm, trên chuỗi quý |
| Tỷ lệ cổ đông thiểu số | Trung vị kỳ có LNST > 0 | Tổng CĐKKS 4 quý / tổng LNST 4 quý |
| Neo năm trước (phi tài chính) | Năm +1: w × 0,5; năm +2: w × 0,25 | w = 0,75 × min(số quý tới, 4)/4 × min(1, doanh thu quý q−4 / bình quân 4 quý) |
| Thuế, cổ tức, capex, khấu hao | Trung vị / chính sách | Theo năm (`annual_only`) |
| Lịch sử ngắn | Năm < 5, quý < 8 quan sát: dùng kỳ liền trước | |

Default ngoài miền hợp lệ chỉ cảnh báo; số analyst nhập ngoài miền bị chặn. Không có default vững thì `needs_input`,
không tự tạo số.

## 5. Khoản bất thường

- Chỉ quét dòng ngoài hoạt động lõi: DT tài chính, CP tài chính khác, LDLK, lợi nhuận khác. Doanh thu, giá vốn, chi phí
  lõi không tự tách.
- DT tài chính và LDLK: **đột biến cô lập** = quý ≥ 5 × trung vị 8 quý trước và quý sau đã về ≤ 2× (chiều giảm đối
  xứng); quý mới nhất chặn trần 5×. Dòng khác: trend OLS 12 quý ± 3σ.
- Ngưỡng tối thiểu |k| ≥ 0,5% doanh thu quý. Tự áp, không chờ xác nhận; công bố từng khoản (ONE-03); analyst bỏ qua được
  từng khoản.
- Ngân hàng: thu nhập ngoài lãi khác không tự làm sạch (chỉ phát hiện, analyst xác nhận).

## 6. Kiểm tra và cờ

| Mức | Ví dụ | Ý nghĩa |
|---|---|---|
| Lỗi chặn | `unbalanced`, `negative_balance`, `unowned_line`, `unmapped_balance`, CAL-03 | Không được xuất |
| Cảnh báo | `funding_gap`, `negative_npat`, `default_out_of_range`, INT-01, AUTO-02 | Xem lại trước khi dùng |
| Thông tin | ONE-02, AUTO-01, chênh lệch cộng quý so với năm | Công bố |

Nhãn **có thể công bố** chỉ khi: không lỗi chặn, BS cân, tiền khớp, không funding gap. Mô hình không đặt sàn lợi nhuận
hay trần tăng trưởng tùy tiện. (Cổng chất lượng cho luồng đại trà và dải sai số theo backtest: xem mục 11.)

## 7. Backtest và dựng lại báo cáo

Bước 1 (một lần, cần owner cho phép chạy trên BigQuery): sinh SQL bằng `tools/gen_backtest_sql.py`, chạy trên console,
lưu CSV vào `data/for_backtest/<nhóm>/`.

```bash
.venv/Scripts/python tools/run_backtest.py run --workers 6
```

```bash
.venv/Scripts/python tools/run_backtest.py report
```

- Quý cắt cuốn chiếu từ 2021Q4; tại mỗi quý cắt mô hình chỉ thấy dữ liệu đến quý đó, dự phóng 8 quý và các năm tới.
- Đối chứng (dự phóng ngây thơ): quý tới = cùng quý năm trước; năm đang chạy = quý đã công bố + cùng quý năm trước;
  năm +1 = năm gốc.
- Thước đo: sai số trung vị (median APE), WAPE, thiên lệch, gộp nhóm, sai số phần còn lại của năm; đếm riêng ca bùng
  nổ (|sai số LNST mẹ| > vốn chủ). Bất động sản không tính vào thống kê.
- Thử nghiệm không sửa cấu hình: `--anchor`, `--defaults`, `--qe`, `--seasonal-shrink` (JSON ghi đè), so sánh bằng
  `tools/compare_backtest.py`.
- Báo cáo HTML có bộ chọn kỳ thống kê (từng năm, 2022/2023/2024 – nay, tất cả).

## 8. Quy trình thay đổi mô hình

L1 phi tài chính đang **khóa** (`tests/test_l1_frozen.py`, dấu vân tay toàn bộ phần L1 của template).

1. Backtest chỉ ra nhóm sai số; nghiên cứu nguyên nhân trên các ca cụ thể.
2. Thử các phương án bằng tùy chọn thử nghiệm của backtest (không sửa cấu hình), so sánh trên cùng tập dự phóng.
3. **Owner duyệt** phương án.
4. Cập nhật spec trước: tăng phiên bản, thêm dòng nhật ký quyết định (lý do, số liệu trước/sau).
5. Sửa cấu hình/code; cập nhật dấu vân tay trong `tests/test_l1_frozen.py`.
6. Chạy test:

```bash
.venv/Scripts/python -m pytest -q
```

7. Chạy lại backtest, xuất báo cáo, lưu file so sánh vào `docs/backtest/`.

Thay đổi ở nhóm khác (ngân hàng, template ngành L2/L3) phải giữ `test_l1_frozen` xanh. Không commit khi owner chưa yêu cầu.

## 9. Lịch sử phiên bản

| Phiên bản | Ngày | Vấn đề phát hiện | Thay đổi | Kết quả |
|---|---|---|---|---|
| — | 12 – 16/11 | Cần khung chung cho nhiều ngành | Bốn nhóm BCTC; một L1 cho cả nhóm phi tài chính; cân nguồn tự động; khoản bất thường; hồ sơ mô hình bắt buộc | Kiến trúc OOP |
| v1.0 | 17/11 | Giữ nguyên một giá trị không hợp lý cho nhiều năm | Đường hội tụ về EMA 5 năm; tăng trưởng dài hạn min(EMA, 8%); CPBH/CPQL cố định + biến đổi; giải thích ô | Owner duyệt mô hình HPG |
| v1.1 | 18/11 | Số năm phải giống nhau khi xem theo quý | Phân bổ năm → quý theo mùa vụ | Không bắt được đà ngắn hạn |
| v2.0 | 18/11 | Phân bổ bỏ lỡ đà gần nhất | Engine quý đến hết Q4 năm sau rồi năm; default quý = 0,65 × EMA 8 quý + 0,35 × năm; tự làm sạch bất thường | Một vòng tính thống nhất |
| v2.1 | 18/11 | Chuỗi ngắn không đủ cho EMA | Dùng kỳ liền trước | Mã mới niêm yết chạy được |
| v2.2 | 18/11 | Doanh thu quý chưa bắt mức mới khi mở rộng công suất (HPG Dung Quất 2) | Ba thành phần QoQ/YoY/năm 0,50/0,15/0,35 trên nền điều chỉnh mức 2 quý | Nhận nền mới sau 1–2 quý |
| v2.3 | 18/11 | Backtest 272 mã: 40 mã bùng nổ khi nền gần 0; mã không có nợ vay | Trần doanh thu 1,5 × bình quân 4 quý; default ngoài miền chỉ cảnh báo; lãi suất 0 khi không có nợ | Hết bùng nổ do doanh thu dồn cục |
| v2.4 | 19/11 | Q4 bị dự phóng cao có hệ thống (mùa vụ 10 năm mạnh hơn hiện tại) | Co biên độ mùa vụ α = 0,6 | Thiên lệch doanh thu Q4 +10,5% → +4,7%; LNST Q4 +14,0% → +5,2% |
| v2.5 | 19/11 | EMA của tỷ lệ có đuôi lỗi lớn khi doanh thu quý gần 0 | EMA tử số / EMA mẫu số; biên gộp quý TTM + 0,25 quán tính; DT tài chính theo mức | Biên gộp sai số TB 9,5 → 5,0 điểm % |
| v2.5.1 | 19/11 | DT tài chính dự phóng thấp (thiên lệch −24%); quy tắc 3σ loại nhầm mức mới | Bình quân 4 quý + đột biến cô lập + trần 5× (13 cách xử lý outlier đã thử) | Sai số 41,0% → 37,2%; thiên lệch −21,6% → −12,7% |
| v2.6 | 19/11 | Kém dự phóng ngây thơ ở đầu năm khi chu kỳ đổi chiều | Neo về cùng quý năm trước theo số quý còn lại; LDLK theo mức | LNST cả năm (cùng tập) 42,5% → 37,3%; năm +1 67% → 57% |
| v2.6.1 | 20/11 | Quý tới bị neo quá nặng (LNST 1 quý tới WAPE 65% → 75%) | Trọng số neo theo số quý tới | 1 quý tới WAPE 75% → 68% |
| v2.6.2 | 20/11 | Lãi vay bùng nổ khi dư nợ đầu kỳ gần 0 (CTD: lãi suất ~4.590%/năm) | Lãi suất trên dư nợ bình quân | Lãi vay 1 quý tới WAPE 43% → 22%; ca bùng nổ 38 → 16 |
| v2.6.3 | 20/11 | Tỷ lệ CĐKKS phóng đại khi LNST gần 0 (HU1, VID); biên gộp từ quý doanh thu gần 0 (BTP) | CĐKKS = tổng 4 quý / tổng 4 quý; quán tính biên và neo theo trọng số doanh thu | CĐKKS 1 quý tới WAPE 68% → 47%; ca bùng nổ 16 → 4 |
| v2.6.4 | 23/11 | Ngân hàng: CK đầu tư mặc định tăng 24%/năm (ACB) nhanh hơn huy động 11–13%, funding gap ~182 nghìn tỷ; lỗi chia cho 0 ở nhánh biên | CK đầu tư = min(EMA5, tăng trưởng huy động); chặn chia 0 / inf | Backtest 25 NH: MAE năm 1 30,6 → 26,2 pp; ACB gap 182 → 12 nghìn tỷ. Phi tài chính không đổi |
| v2.6.5 | 25/11 | Demo HPG: sửa tăng trưởng doanh thu 2027 làm doanh thu 2026 và 2027 giảm (năm 2026 giữ giá trị mặc định bị coi là mục tiêu cả năm, ép Q4/2026 còn 5.249 tỷ) | Chỉ các năm override khác default mới là số analyst nhập (`override_years`, suy ra ở `resolve`) | Sửa một năm không đổi các năm trước; mô hình mặc định không đổi |
| v2.6.6 | 26/11 | Lãi vay quý chậm theo lãi suất mới; chi phí dự phòng ngân hàng chỉ đi theo năm nên lệch ở quý tới | Phi tài chính: lãi suất vay quý = quý gần nhất. Ngân hàng: chi phí tín dụng quý = 0,65 × TB 4 quý + 0,35 × default năm | Lãi vay Q+1 WAPE 20,1% → 14,5%; dự phòng NH Q+1 43,6% → 36,2%, LNST NH Q+1 21,8% → 20,0% (năm tới 16,9% → 18,3%, mẫu 25 NH) |

Ngân hàng: thiết kế duyệt 17 – 18/11 (EMA5 có giảm dần về 15%/13%, trần LDR, cân thanh khoản qua liên ngân hàng); dùng
engine quý + năm từ 18/11 (AEY, COF theo quý); thu nhập ngoài lãi khác theo mức và không tự làm sạch từ 19/11.

Số liệu “cùng tập” lấy từ backtest mỗi phiên bản trên cùng tập dự phóng (phi tài chính, không BĐS); các con số khác lấy
từ nhật ký quyết định của spec, đo tại thời điểm quyết định.

## 10. Danh mục tình huống biên

| Tình huống | Ví dụ | Cách xử lý | Trạng thái |
|---|---|---|---|
| Mở rộng công suất, nền mới | HPG | Nền điều chỉnh 2 quý + bước QoQ | Đã xử lý |
| Mùa vụ thay đổi theo thời gian | Q4 nhiều ngành | Co biên độ mùa vụ 0,6 | Đã xử lý |
| Doanh thu quý gần 0 | Thủy điện mùa khô, BTP | Tỷ lệ theo trọng số doanh thu | Đã xử lý |
| Doanh thu dồn cục, nền gần 0 | 40 mã ở backtest đầu | Trần 1,5 × bình quân 4 quý | Đã xử lý |
| Thu nhập tài chính đột biến | HPG 2026Q1 (~10×) | Đột biến cô lập + trần 5× | Đã xử lý |
| Mức thu nhập tài chính mới thật | HPG 2021 | Quý sau vẫn cao thì giữ | Đã xử lý |
| Dư nợ đầu kỳ gần 0 | CTD 2022 | Lãi suất trên dư nợ bình quân | Đã xử lý |
| LNST lịch sử gần 0 | HU1, VID, SMC, FCN | CĐKKS = tổng 4 quý / tổng 4 quý | Đã xử lý |
| Lịch sử ngắn | Mã mới niêm yết | Kỳ liền trước; gắn cờ mẫu yếu | Đã xử lý |
| Không có nợ vay trong lịch sử | — | Lãi suất 0, vay tự động ngắn hạn, cảnh báo INT-01 | Đã xử lý |
| Chu kỳ đổi chiều | Thép 2022 | Neo về cùng quý năm trước | Giảm một phần |
| Trích lập, quyết toán cuối năm | LNST Q4 | Đã thử tỷ lệ Q4 năm trước (kém hơn); công bố | Giới hạn |
| Doanh nghiệp sụp đổ hoạt động | TCO | Không dự phóng được từ lịch sử | Giới hạn |
| Năm +1 ngoại suy từ năm đột biến | NKG | Neo năm +1 × 0,5 | Giảm một phần |

## 11. Giới hạn đã biết và hướng phát triển

**Giới hạn** (đầy đủ ở spec mục 11): không thấy trước cú sốc vĩ mô; năm +1 còn kém dự phóng ngây thơ; LNST Q4 lệch do
trích lập cuối năm; DN nhỏ và sự kiện riêng (M&A, tái cấu trúc, sụp đổ); phần lớn dòng BS ngoài VLĐ/TSCĐ/nợ/vốn chủ giữ
nguyên số dư; thuế đã nộp = thuế kỳ; dữ liệu không point-in-time.

**Hướng phát triển** (chưa triển khai):

1. **Tinh chỉnh đầu ra cho luồng đại trà**: dải sai số hiệu chỉnh từ backtest (theo dòng, tầm dự phóng, quy mô, ngành)
   thay dải ±10% cố định; cổng chất lượng gắn cờ mã chưa đủ tin cậy trước khi xuất (lịch sử ngắn, nhiều khoản bất
   thường, mẫu số gần 0, funding gap).
2. **Thư viện + API cho IT**: đóng gói engine phi tài chính và ngân hàng; IT dựng luồng sub-agent phía họ trên cùng dữ
   liệu GBQ.
3. **Lớp nhận định ngành của analyst** (đề xuất): analyst đặt vài tham số ngành (tăng trưởng doanh thu, điều chỉnh biên
   gộp, chi phí vốn) cho kịch bản cơ sở và xấu; tham số đi vào mọi mã trong ngành qua cơ chế ghi đè giả định hiện có
   (nguồn `analyst`), vẫn giải thích được và khôi phục default được.

---
Tài liệu nội bộ; cần rà soát và kiểm chứng trước khi dùng ngoài bộ phận hoặc ban hành.
