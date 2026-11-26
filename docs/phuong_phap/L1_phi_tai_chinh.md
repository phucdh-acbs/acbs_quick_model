# Mô hình L1 doanh nghiệp phi tài chính — bản chốt v2.6.6

| | |
|---|---|
| Trạng thái | **Chốt** v2.6.6 (owner 2026-11-26: lãi suất vay quý mặc định = lãi suất quý gần nhất (`quarter_stats.interest_rate`, cửa sổ 1, λ = 1) thay 0,65 × EMA 8 quý + 0,35 × năm; v2.6.3 owner 2026-11-20: tỷ lệ CĐKKS quý = tổng CĐKKS 4 quý / tổng LNST 4 quý; quán tính biên gộp theo trọng số doanh thu; neo năm trước theo trọng số mẫu số của quý được neo; v2.6.2 owner 2026-11-20: lãi suất mặc định trên dư nợ bình quân đầu + cuối kỳ; v2.6.1 owner 2026-11-20: trọng số neo của năm đang chạy theo số quý tới, quý gần neo ít; v2.6 owner 2026-11-19: neo giả định quý về cùng quý năm trước theo số quý còn lại của năm, giảm dần ở năm +1, +2; LDLK theo mức; v2.5.1 owner 2026-11-19: DT tài chính quý = bình quân 4 quý; khoản bất thường của DT tài chính theo quy tắc đột biến cô lập + trần quý mới nhất; v2.5 owner 2026-11-19: EMA quý có trọng số cho tỷ lệ trên doanh thu/giá vốn, biên gộp quý = TTM + 0,25 × (quý gần nhất − TTM), doanh thu tài chính theo mức trung vị 4 quý; v2.4 owner 2026-11-19: co biên độ mùa vụ doanh thu α = 0,6 sau backtest; v2.3 owner 2026-11-18, sau backtest; v2.2 owner chốt cùng ngày; v1.0 owner duyệt 2026-11-17; v1.1 sáng 2026-11-18: phân bổ năm -> quý; v2.0 cùng ngày: engine quý + năm, default quý = 0,65 × EMA 8 quý + 0,35 × năm, tự làm sạch khoản bất thường; v2.1: chuỗi chưa đủ cho EMA dùng số kỳ liền trước; v2.2: doanh thu quý 3 thành phần QoQ/YoY/năm) |
| Phạm vi | Mọi doanh nghiệp thuộc nhóm BCTC phi tài chính (`family = nonfinancial`), mọi template ngành kế thừa (`manufacturing`, `retail`, `construction`, …) |
| Nguồn cấu hình | `config/templates/nonfinancial/template.json` (method, giả định, `default_paths`, `one_off`, `quarterly_engine`), `config/families/nonfinancial/*.csv` (dòng BCTC), `config/methods/library.json` |
| Code | `quick_model/families/nonfinancial/` (engine, method, cân nguồn, chỉ số lịch sử), `quick_model/engine/defaults.py`, `quick_model/oneoff.py`, `quick_model/engine/bands.py`, `quick_model/hybrid.py`, `quick_model/explain.py` |
| Mô hình mẫu đã duyệt | HPG, `docs/models/HPG/HPG_base_Y.*` |

Tài liệu này mô tả phương pháp chung. Số liệu của từng mã nằm trong bộ tài liệu mô hình của mã đó
(`tools/export_model_doc.py`), không nằm ở đây.

## 1. L1 dùng để làm gì

- Dự phóng nhanh, chạy tự động cho mã chưa có analyst cover: mọi giả định có default tính từ lịch sử BCTC, không cần nhập tay.
- Là mức sàn của mọi block: kể cả khi analyst dùng L2/L3 cho doanh thu, các block còn lại vẫn có thể giữ L1.
- Tất định: cùng dữ liệu và cùng giả định thì mọi người ra cùng một số. Không có yếu tố ngẫu nhiên.
- Không phải khuyến nghị đầu tư, không phải định giá, không thay mô hình chi tiết của analyst.

**Một L1 cho cả nhóm.** Mọi template ngành phi tài chính dùng chung đúng một bộ method L1 và một bộ giả định L1.
Template ngành chỉ thêm L2/L3. Test `tests/test_template.py` kiểm tra L1 giống hệt nhau ở mọi template, và
`tests/test_l1_frozen.py` khóa dấu vân tay của bản chốt (mục 9).

## 2. Dữ liệu đầu vào

| Nội dung | Quy ước |
|---|---|
| Nguồn | BigQuery chỉ đọc qua lớp adapter; bản hiện tại chạy trên dữ liệu thô đã tải về (`companies.json` → thư mục dữ liệu). |
| Phạm vi | BCTC hợp nhất; không trộn hợp nhất với riêng lẻ, không trùng phiên bản báo cáo. |
| Kỳ theo năm | Báo cáo năm (FY) và số dư 31/12. Năm thiếu báo cáo FY thì engine năm không dùng (chưa cộng 4 quý). |
| Kỳ theo quý | Quý đứng riêng (dùng cho cột thực tế khi xem theo quý, lũy kế năm hiện tại, mùa vụ phân bổ và phát hiện khoản bất thường); dòng IS/CF lũy kế được chuyển về quý đứng riêng sau khi kiểm tra; số dư BS lấy tại thời điểm, **không lấy hiệu số dư**. |
| Kỳ gốc | Kỳ công bố gần nhất của tần suất đó, lấy từ dữ liệu (`default_origin`), không gán cứng. |
| Kiểm tra lịch sử | Đẳng thức BCTC, tiền LCTT khớp tiền BS, cộng quý so với năm, liên tục số dư tiền, trùng dòng BS, độ phủ (`quick_model/data/checks.py`). Chênh lệch giữa tổng quý và năm kiểm toán ghi mức `info` và cho qua. |

Lịch sử thật không bị sửa để cân. Chênh lệch có thật được báo, không bị xoá.

## 3. Cấu trúc: 8 block, mỗi block một method L1

| Block | Method L1 (`method_id`) | Công thức chính |
|---|---|---|
| Doanh thu | `revenue.L1.yoy_growth` | Doanh thu năm = doanh thu năm trước × (1 + g). |
| Giá vốn | `cogs.L1.gross_margin` | Giá vốn = −(1 − biên gộp) × doanh thu. |
| CPBH & CPQL | `sga.L1.cost_structure` | Mỗi loại = −(cố định + tỷ lệ biến đổi × doanh thu). |
| Vốn lưu động | `working_capital.L1.days` | Phải thu = doanh thu × DSO / số ngày kỳ; tồn kho = giá vốn × DIO / số ngày kỳ; phải trả = giá vốn × DPO / số ngày kỳ (**xấp xỉ theo giá vốn**, không phải giá trị mua hàng). Trả trước người bán, người mua trả trước: giữ số dư. |
| TSCĐ | `ppe.L1.capex_intensity` | Capex = −capex/doanh thu × doanh thu; khấu hao = tỷ lệ năm × (TSCĐ + dở dang) đầu kỳ × ngày/365; TSCĐ cuối = đầu kỳ + capex − khấu hao; dở dang dài hạn giữ nguyên (không tách XDCB). |
| Nợ vay | `debt.L1.auto_balance` | Lãi vay = −lãi suất năm × tổng vay đầu kỳ × ngày/365. Dư nợ hiện hữu giữ nguyên; vay/trả tự động theo tiền tối thiểu trong trần nợ (mục 6). |
| Ngoài HĐKD | `non_operating.L1.policies` | DT tài chính = mức DT tài chính theo năm × ngày/365 (v2.5, thay suất sinh lời × số dư); CP tài chính ngoài lãi vay = −tỷ lệ × doanh thu; liên doanh liên kết và lợi nhuận khác = số khai báo (mặc định 0); cộng khoản bất thường đã xác nhận (mục 7). |
| Thuế & vốn chủ | `tax_equity.L1.policies` | Thuế = −thuế suất hiệu dụng × LNTT nếu LNTT > 0, lỗ thì 0; LNST cổ đông thiểu số = tỷ lệ × LNST; cổ tức tiền = −tỷ lệ chi trả × LNST công ty mẹ (nếu > 0); VCSH mẹ cuối = đầu kỳ + LNST mẹ + phát hành + cổ tức (âm); LICĐKKS cuối = đầu kỳ + LNST thiểu số. |

Lãi vay tính trên số dư **đầu kỳ** để tránh vòng lặp lãi vay – nợ – tiền. Không có bộ giải lặp. Từ v2.5 DT tài chính
theo mức (không phụ thuộc số dư tiền dự phóng), nên không tạo vòng lặp.

Các dòng BS còn lại (ĐTNH, phải thu khác, TS ngắn hạn khác, phải thu dài hạn, BĐS đầu tư, đầu tư dài hạn,
TS dài hạn khác, nợ ngắn/dài hạn khác, quỹ khen thưởng phúc lợi, …) **giữ nguyên số dư kỳ gốc** và được công
bố là giản lược (`owner = carry` trong `bs_lines.csv`). Không có dòng "tài sản/nợ khác" làm biến cân.

## 4. Liên kết ba báo cáo

```
Doanh thu → giá vốn → LN gộp → CPBH, CPQL → DT/CP tài chính, LDLK → LN khác → LNTT → thuế → LNST → LNST mẹ
Doanh thu, giá vốn + số ngày → phải thu, tồn kho, phải trả
Doanh thu + capex/DT → capex → TSCĐ; tỷ lệ khấu hao → khấu hao
LCTT: CFO = LNTT + khấu hao + Δphải thu + Δtồn kho + Δphải trả + thuế đã nộp (= thuế kỳ) + khác
      CFI = capex
      CFF = phát hành + vay tự động − trả vay tự động + cổ tức
Tiền cuối = tiền đầu + CFO + CFI + CFF (chênh lệch tỷ giá = 0)  →  bằng đúng tiền trên BS
```

Mỗi kỳ dự phóng engine kiểm tra: BS cân (tổng tài sản − tổng nguồn vốn trong dung sai), tiền LCTT khớp tiền BS,
không có dòng BS nào thiếu method ghi, không có số dư vốn lưu động âm.

Giản lược cần công bố: thuế đã nộp = thuế kỳ; capex tiền = tăng TSCĐ (không tách phần phi tiền mặt); khoản bất
thường xác nhận được coi là tiền; phải trả theo giá vốn.

## 5. Giả định và default

### 5.1 Bảng giả định L1

| Giả định | ID | Đơn vị | Default |
|---|---|---|---|
| Tăng trưởng doanh thu | `revenue_yoy_growth` | tỷ lệ | Hội tụ: từ YoY thực tế gần nhất về `long_run_growth`, đạt ở năm thứ 5 tính từ kỳ dự phóng đầu, giữ từ đó |
| Tăng trưởng dài hạn (mức neo) | `long_run_growth` | tỷ lệ | min(EMA 5 năm của YoY doanh thu, 8%); một giá trị, không theo kỳ |
| Biên gộp | `gross_margin` | tỷ lệ | Hội tụ về EMA trong 4 năm |
| CPBH biến đổi / doanh thu | `selling_expense_ratio` | tỷ lệ | Chi phí biên (hồi quy, mục 5.4) |
| CPBH cố định mỗi kỳ | `selling_fixed_cost` | VND | CPBH kỳ gốc − chi phí biên × doanh thu kỳ gốc |
| CPQL biến đổi / doanh thu | `admin_expense_ratio` | tỷ lệ | Như CPBH |
| CPQL cố định mỗi kỳ | `admin_fixed_cost` | VND | Như CPBH |
| Số ngày phải thu | `dso_days` | ngày | Hội tụ về EMA trong 4 năm |
| Số ngày tồn kho | `dio_days` | ngày | Hội tụ về EMA trong 4 năm |
| Số ngày phải trả (theo giá vốn) | `dpo_days` | ngày | Hội tụ về EMA trong 4 năm |
| Capex / doanh thu | `capex_to_revenue` | tỷ lệ | Hội tụ về EMA trong 4 năm |
| Tỷ lệ khấu hao năm | `depreciation_rate` | tỷ lệ/năm | Trung vị 3 năm gần nhất: khấu hao LCTT quy năm / (TSCĐ + dở dang) đầu kỳ |
| Lãi suất vay | `interest_rate` | tỷ lệ/năm | Thực tế kỳ liền trước: chi phí lãi vay / tổng vay bình quân (đầu kỳ + cuối kỳ) / 2, quy năm (v2.6.2, metric `interest_rate_avg`; trước đó chia cho vay đầu kỳ). Lãi vay dự phóng vẫn = lãi suất × dư nợ đầu kỳ (không vòng lặp). Quý (v2.6.6): lãi suất quý gần nhất, không gộp EMA 8 quý và không gộp giá trị năm; các quý sau giữ mức đó, năm dùng đường default năm như cũ |
| Tiền tối thiểu / doanh thu | `min_cash_ratio` | tỷ lệ | Thấp nhất lịch sử trong cửa sổ |
| Tỷ trọng vay ngắn hạn trong vay tự động | `auto_st_share` | tỷ lệ | Cơ cấu vay ngắn/tổng vay kỳ gốc |
| Trần tổng vay / tổng tài sản | `max_debt_to_assets` | tỷ lệ | Cao nhất lịch sử trong cửa sổ (tối đa 95%) |
| Mức DT tài chính theo năm | `financial_income_run_rate` | VND/năm | Năm: DT tài chính năm gần nhất (đã loại khoản bất thường). Quý (v2.5.1): bình quân 4 quý gần nhất của DT tài chính quy năm (đã xử lý khoản bất thường, mục 7) (× 365 / số ngày), cuốn chiếu; các năm sau phần quý giữ mức quý cuối |
| CP tài chính ngoài lãi vay / doanh thu | `other_financial_expense_ratio` | tỷ lệ | Hội tụ về EMA trong 4 năm |
| Mức lãi/lỗ LDLK theo năm | `jv_associates_run_rate` | VND/năm | Năm: LDLK năm gần nhất (đã loại khoản bất thường). Quý (v2.6): bình quân 4 quý của LDLK quy năm, neo năm trước |
| Lợi nhuận khác | `other_profit` | VND | Chính sách 0 |
| Thuế suất hiệu dụng | `effective_tax_rate` | tỷ lệ | Trung vị cửa sổ gần nhất, chỉ kỳ có LNTT > 0 |
| Tỷ lệ LNST cổ đông thiểu số | `minority_share` | tỷ lệ | Năm: trung vị cửa sổ gần nhất, chỉ kỳ có LNST > 0. Quý (v2.6.3): tổng CĐKKS 4 quý / tổng LNST 4 quý, λ = 1 |
| Tỷ lệ cổ tức tiền / LNST mẹ | `cash_dividend_payout` | tỷ lệ | Chính sách 0; analyst khai theo kế hoạch cổ tức |
| Tiền thu phát hành cổ phiếu | `share_issue_proceeds` | VND | Chính sách 0 |

Nguồn của mỗi giá trị được ghi riêng: `historical_default`, `explicit_policy`, `analyst`, `auto_balance` hoặc
`needs_input`. Để trống khác với 0. Analyst sửa thì số của analyst được ưu tiên đến khi bấm khôi phục default.

### 5.2 Đường hội tụ (converge)

Áp cho: tăng trưởng doanh thu, biên gộp, DSO, DIO, DPO, capex/doanh thu, CP tài chính ngoài lãi vay/doanh thu.

- **Điểm đầu** = mức thực tế năm gần nhất (tăng trưởng: YoY năm gần nhất).
- **Mức neo** = EMA, span 5 năm, trên tối đa 10 năm lịch sử gần nhất (năm gần nặng hơn, outlier xa nhẹ hơn).
- **Lộ trình** = đi tuyến tính từ điểm đầu đến mức neo trong `fade_years` = 4 năm (tăng trưởng: 5 năm), tính từ
  kỳ dự phóng đầu, **không** theo số năm dự phóng. Dự phóng 2 năm vẫn chỉ đi 2/4 quãng đường.
- Tăng trưởng doanh thu neo về `long_run_growth` = min(EMA 5 năm YoY, 8%). Analyst sửa mức neo thì cả đường được
  tính lại. Không có YoY thực tế làm điểm đầu thì kỳ đó là `needs_input`, không lấy mức neo thay.
- Mỗi kỳ ghi lại kỳ nguồn, điểm đầu, mức neo, trọng số, giá trị (`basis["path"]`) để giải thích.

Tham số trong `default_paths`: `fade_years` 4, `anchor_window_years` 10, `anchor_stat` "ema", `ema_span_years` 5,
`cost_structure_window_years` 8, `cost_structure_min_obs` 4 (các tham số theo quý `seasonal_years`,
`cost_structure_window_quarters` còn trong cấu hình nhưng không dùng từ v1.1).

### 5.2b Lịch sử ngắn (v2.1, owner 2026-11-18)

Chuỗi chưa đủ độ dài cho EMA (năm: ít hơn 5 quan sát; quý: ít hơn 8 quý) thì dùng số kỳ liền trước, cả cho mức neo
(`default_paths.short_history = "latest"`). Đường hội tụ và giảm dần vẫn áp như cũ từ mức đó (vd tăng trưởng dài hạn =
min(YoY năm liền trước, 8%)). Thống kê trung vị/thấp nhất/cao nhất theo cửa sổ vẫn dùng các mẫu có sẵn (gắn cờ mẫu yếu).

### 5.3 Mùa vụ và default quý

Default năm (mục 5.1–5.5) giữ nguyên. Các quý của phần chạy theo quý (mục 8) dùng default quý = 0,65 × EMA span 8
quý liên tiếp + 0,35 × default năm của năm chứa quý; mùa vụ đi vào qua chuỗi quý (tỷ lệ của các quý gần nhất), không
có tham số mùa vụ riêng.

v2.5 (owner 2026-11-19, sau backtest):
- **EMA quý có trọng số** cho giả định dạng tỷ lệ trên doanh thu hoặc giá vốn (biên gộp, tỷ lệ biến đổi CPBH/CPQL, CP
  tài chính ngoài lãi vay / doanh thu, DSO, DIO, DPO): EMA 8 quý của tử số / EMA 8 quý của mẫu số, thay EMA của tỷ lệ.
  Quý doanh thu gần 0 (thủy điện mùa khô, DN ngừng hoạt động) không còn kéo tỷ lệ cực trị sang các quý sau. Quý dự
  phóng nối vào chuỗi với mẫu số = EMA mẫu số hiện hành (trọng số trung tính). Default năm (EMA 5 năm) giữ nguyên.
- **Biên gộp quý** = TTM + 0,25 × (biên quý gần nhất − TTM); TTM = lãi gộp 4 quý / doanh thu 4 quý; λ = 1 (không gộp
  giá trị năm), cuốn chiếu; các năm sau phần quý dùng đường hội tụ năm như cũ. v2.6.3: phần quán tính tính theo trọng
  số doanh thu, = 0,25 × (lãi gộp quý gần nhất − TTM × doanh thu quý gần nhất) / doanh thu bình quân 4 quý (quý doanh
  thu gần 0 gần như không tác động; vd BTP 2022Q1 doanh thu 0,1 tỷ, biên −25.900%).
- **DT tài chính quý** = trung vị 4 quý gần nhất của DT tài chính quy năm; λ = 1, các năm sau giữ mức quý cuối.

v2.6 (owner 2026-11-19, sau backtest v2.5.1): **neo năm trước** (`quarterly_engine.anchor`). So với dự phóng ngây thơ
"các quý còn lại = cùng quý năm trước", mô hình kém hơn ở đầu năm và sau Q1 (LNST mẹ gộp cả nhóm lệch 40% so với 25%
ở đầu năm) dù từng dòng xấp xỉ: mô hình ngoại suy đà của năm gần nhất cùng lúc cho doanh thu, biên, chi phí nên khi chu
kỳ đổi chiều mọi dòng lệch cùng phía; đổi riêng cách tính biên gộp không cải thiện. Quy tắc:
- Với mỗi quý dự phóng q và giả định trong danh sách neo: x_q = (1 − w) × default quý + w × neo_q.
- neo_q = giá trị của chính giả định ở cùng quý năm trước (q − 4): thực tế (chuỗi quý đã xử lý khoản bất thường) nếu
  q − 4 đã công bố, ngược lại là giá trị đã neo của q − 4. Tăng trưởng doanh thu so cùng kỳ neo về 0 (doanh thu = cùng
  quý năm trước). Danh sách: tăng trưởng doanh thu, biên gộp, tỷ lệ biến đổi CPBH/CPQL, CP tài chính khác / doanh thu,
  mức DT tài chính, mức LDLK.
- v2.6.1 (owner 2026-11-20, `weight_by: horizon`): w = 0,75 × min(h, 4) / 4 cho các quý của năm đang chạy, h = số quý
  tính từ quý công bố mới nhất (quý tới 0,19, quý thứ hai 0,38, …): quý gần dùng nhiều thông tin gần đây hơn. (v2.6:
  w = 0,75 × số quý còn lại của năm / 4, như nhau cho mọi quý của năm.) Năm +1: 0,75 × 0,5; năm +2 trở đi: 0,75 × 0,25 (phần chạy theo năm: neo về bình quân 4
  quý của năm trước, tăng trưởng về 0). Trọng số đổi liên tục theo số quý còn lại: không có điểm gãy khi công bố quý.
- v2.6.3 (`den_weighted`): với giả định là tỷ lệ trên doanh thu, w nhân thêm min(1, doanh thu quý q − 4 / doanh thu
  bình quân 4 quý gần nhất): quý năm trước có doanh thu gần 0 thì neo gần như không dùng tỷ lệ của quý đó.
- Chỉ áp cho default (số analyst nhập không bị neo); disclosed trong basis/trace của giả định. Ngân hàng không áp (mô
  hình ngân hàng tốt hơn dự phóng ngây thơ ở mọi thời điểm).
- **LDLK theo mức** (`jv_associates_run_rate`, VND/năm): thay chính sách 0 bằng mức LDLK quy năm, quý = bình quân 4 quý
  (λ = 1), khoản bất thường theo quy tắc đột biến cô lập như DT tài chính. Lợi nhuận khác giữ chính sách 0 (dự phóng theo
  năm trước còn kém hơn 0).

v2.5.1 (owner 2026-11-19, sau backtest v2.5): trung vị 4 quý cắt các quý cao (cổ tức, lãi thanh lý về theo đợt) nên
DT tài chính bị dự phóng thấp có hệ thống (bias năm 0 −24%). **DT tài chính quý = bình quân 4 quý** của DT tài chính
quy năm, sau khi xử lý khoản bất thường theo quy tắc đột biến cô lập (mục 7).

### 5.4 CPBH, CPQL: cố định + biến đổi

- Tỷ lệ biến đổi = chi phí biên, hồi quy qua gốc Δchi phí theo Δdoanh thu so năm trước, 8 năm, tối thiểu 4 quan sát.
- Cố định = chi phí kỳ gốc − tỷ lệ biến đổi × doanh thu kỳ gốc.
- Đòn bẩy hoạt động chỉ đến từ phần cố định đã công bố này. Biên không tự tăng chỉ vì doanh thu tăng.
- Hồi quy không hợp lệ (thiếu mẫu, hệ số âm) thì cố định = 0, tỷ lệ = trung vị chi phí/doanh thu, ghi rõ.

### 5.5 Các giả định không hội tụ

Lãi suất vay lấy thực tế kỳ liền trước (không lấy trung vị); DT tài chính theo mức (mục 5.1, 5.3). Thuế suất, tỷ lệ cổ đông
thiểu số, tỷ lệ khấu hao lấy trung vị cửa sổ gần nhất. Tiền tối thiểu và trần nợ lấy biên lịch sử (thấp nhất /
cao nhất). Cổ tức, phát hành, lợi nhuận khác là chính sách 0 cho đến khi analyst khai; LDLK theo mức lịch sử (v2.6).

## 6. Cân nguồn tự động (chỉ khi nợ vay ở L1)

**Bước 1: vay tự động trong engine** (`declared_revolver`)
- Dư nợ hiện hữu giữ nguyên.
- Ràng buộc kiểm ở kỳ cuối năm (kỳ năm hoặc Q4; owner 2026-11-18): tiền trước tài trợ < tiền tối thiểu
  (`min_cash_ratio` × doanh thu **cả năm**) thì rút vay, chia ngắn/dài hạn theo `auto_st_share`; tiền dư so với mức
  tối thiểu chỉ dùng trả phần vay tự động đã rút, ngắn hạn trước; tổng vay không vượt `max_debt_to_assets` × tổng tài
  sản. Phần còn thiếu là **funding gap**, công bố, không bù bằng biến cân ẩn.
- Quý 1–3: chỉ rút vay tự động khi tiền trước tài trợ âm (đưa về 0), không trả nợ, không áp trần; funding gap quý giữa
  năm chỉ là phần tiền âm còn lại.

**Bước 2: điều chỉnh giả định sau khi analyst xác nhận** (`balancing.py`)
- Kích hoạt khi tổng vay tăng hơn 15% so với kỳ trước (`AutoBalancePolicy.max_debt_growth`) hoặc còn funding gap.
- Chẩn đoán nguồn dùng tiền (vốn lưu động, capex vượt khấu hao, cổ tức, lợi nhuận) và kéo giả định L1 chịu trách
  nhiệm về phía biên lịch sử: DSO, DIO về phía thấp nhất; DPO về phía cao nhất; capex/doanh thu về phía thấp nhất.
- Đích = trung điểm của trung vị lịch sử và biên (`target = "midpoint"`). Mỗi bước chọn giả định giảm vi phạm
  nhiều nhất. Chỉ kéo những kỳ xấu hơn đích; đường default giữ nguyên ở các kỳ khác.
- Mỗi thay đổi là một override nguồn `auto_balance` kèm lý do (AUTO-01). Số analyst đã nhập và block không ở L1
  không bao giờ bị đổi. Còn vi phạm thì cảnh báo AUTO-02.

Nợ vay ở L2/L3 (lịch vay) thì không tự vay: thiếu tiền báo FUND-L2 và analyst tự cân nguồn.

Kịch bản còn funding gap được đánh dấu **không khả thi**. BS cân về toán học vẫn có thể không khả thi về kinh tế;
màn hình hiện cả hai trạng thái.

## 7. Khoản bất thường (tự làm sạch, v2.0; DT tài chính theo đột biến cô lập, v2.5.1)

- Phạm vi: chỉ dòng ngoài hoạt động lõi (DT tài chính, CP tài chính khác, LDLK, lợi nhuận khác). Doanh thu, giá
  vốn, chi phí lõi không tự tách vì trend tuyến tính không phân biệt được chu kỳ hay mở rộng công suất.
- Quét: 9 năm lịch sử rồi các quý đã công bố của năm hiện tại; mỗi năm so với trend OLS 12 quý (tối thiểu 8) trước
  năm đó trên **chuỗi gốc** (`sequential: false`: không thay quý bị cờ khi quét năm sau, vì với dòng tăng cấu trúc
  như thu nhập tài chính HPG, quét tuần tự kéo trend xuống và đánh dấu nhầm mọi quý sau). Gắn cờ khi |thực tế − trend|
  > 3 × độ lệch chuẩn phần dư **và** |k| ≥ 0,5% doanh thu quý; cả hai chiều.
- k = thực tế − trung bình các quý trong cửa sổ (`replace_with: mean`). Owner 2026-11-18: **tự áp, không đợi xác
  nhận** (`auto_history`, `auto_current`); ONE-03 công bố từng khoản; analyst bỏ qua được từng khoản.
- Loại k khỏi chuỗi làm default: chuỗi năm (trước khi tính EMA, hội tụ…) và chuỗi quý (EMA 8 quý). Quý đã công bố
  giữ số thực tế (gồm k) trong tổng năm; quý dự phóng không cộng lại k.
- **DT tài chính (v2.5.1, `rule: isolated_spike`)**: quy tắc trend ± 3σ không phân biệt khoản một lần với mức mới (HPG
  2021Q1 tiền tăng gấp ba, DT tài chính ở mức cao 2 năm nhưng bị loại; 225 ca có quý ≥ 5× cho bias năm 0 −47%). Thay
  bằng: m = trung vị 8 quý trước quý xét (chuỗi gốc, cần đủ 8 quý, m > 0).
  - **Đột biến cô lập**: quý ≥ 5 × m (`spike_ratio`) **và quý ngay sau đã quay về** ≤ 2 × m (`revert_ratio`): loại,
    mức thay thế = m. Chiều giảm đối xứng: quý ≤ m / 5 và quý sau ≥ m / 2.
  - **Quý công bố mới nhất** (chưa có quý sau để xác nhận): chặn trần 5 × m (`latest_cap`), mức thay thế = 5 × m (sàn
    m / 5 ở chiều giảm). Sang quý sau, quý này được xét lại theo quy tắc đột biến cô lập.
  - Quý cao nhưng quý sau vẫn cao (mức mới) giữ nguyên. Vẫn cần |k| ≥ 0,5% doanh thu quý; tự áp như mục trên (ONE-03).
  - LDLK cũng theo quy tắc đột biến cô lập (v2.6). CP tài chính khác, lợi nhuận khác giữ quy tắc trend ± 3σ.
- Hạn chế: khoản bất thường phi tiền mặt không tách khỏi CFO; database không có thuyết minh nên analyst phải tự
  kiểm tra bản chất khoản mục. Quy tắc đột biến cô lập cần 1 quý sau mới loại hẳn: quý ngay sau khoản một lần vẫn
  chịu phần dưới trần 5 × m.

## 8. Engine quý + năm (v2.0, owner 2026-11-18)

Kịch bản tạo theo năm (năm gốc = báo cáo cả năm mới nhất). Một vòng tính duy nhất (`quick_model/hybrid.py`):

| | Cách tính |
|---|---|
| Phần quý | Từ quý sau quý công bố mới nhất đến hết Q4 năm sau năm của quý đó (không quá năm dự phóng cuối). Số dư đầu = BS quý công bố mới nhất; tăng trưởng so cùng quý năm trước; lãi trên số dư đầu quý; số ngày thực tế của quý |
| Phần năm | Các năm sau, nối tiếp số dư Q4; gốc tăng trưởng = tổng 4 quý của năm trước |
| Số năm của năm có quý | Tổng các quý (quý đã công bố + quý dự phóng); dòng số dư lấy Q4. Vì vậy số năm giống nhau dù xem theo năm hay quý |
| Default quý (giả định có chỉ số lịch sử) | x_q = λ × EMA span 8 quý liên tiếp + (1 − λ) × default năm của năm chứa quý, λ = 0,65. EMA cuốn chiếu: giá trị dự phóng nối vào chuỗi; chuỗi quý tối đa 40 quý, đã loại khoản bất thường. Không có chuỗi quý hợp lệ thì dùng giá trị năm |
| Doanh thu quý (v2.2, phương án E) | R_q = 0,50 × QoQ + 0,15 × YoY + 0,35 × năm. QoQ = R(q−1) × S(q)/S(q−1) × (1 + g_năm)^¼; YoY = B_q × (1 + EMA 8 quý của YoY); năm = B_q × (1 + g_năm). Nền điều chỉnh mức B_q = bình quân doanh thu khử mùa vụ (R/S) của 2 quý liền trước, lùi 2,5 quý về cùng kỳ năm trước theo (1 + g_năm), × S(q). S = tỷ trọng quý trong năm, EMA 5 năm trên tối đa 10 năm đủ 4 quý. Quý dự phóng nối YoY điều chỉnh R_q/B_q − 1 vào chuỗi EMA, nên bước nhảy mức (mở rộng công suất) không bị nhân sang năm sau. Giả định quý hiển thị = R_q / R(q−4) − 1 |
| Co biên độ mùa vụ (v2.4) | Tỷ trọng mùa vụ dùng cho doanh thu quý S' = 0,25 + 0,6 × (S − 0,25) (`seasonal_shrink`), áp cho cả bước QoQ và quy mức năm. Backtest 252 mã (2022–2025): biên độ mùa vụ 10 năm lớn hơn hiện tại, Q4/Q3 mô hình 1,168 so với thực tế 1,062, Q4 bị dự phóng cao có hệ thống. Sau khi co: bias doanh thu Q4 +10,5% → +4,7%, LNST Q4 +14,0% → +5,2%; sai số doanh thu 1 quý tới 17,5% → 16,1% (cả bốn quý tốt lên); đổi lại dự phóng doanh thu năm từ đầu năm 18,3% → 19,4%, LNST năm 50,1% → 49,0% |
| Giới hạn doanh thu dồn cục (v2.3) | Doanh thu quý dự phóng ≤ 1,5 × bình quân 4 quý gần nhất × (4 × tỷ trọng mùa vụ của quý) (`max_to_avg4`). Giới hạn theo mức doanh thu gần đây, không phải trần tăng trưởng; chặn chuỗi nhân liên tiếp khi nền gần 0 (backtest: 40 mã bất động sản/điện/khác bùng nổ) |
| CPBH/CPQL | Cố định năm chia theo ngày; tỷ lệ biến đổi quý gộp như trên với chuỗi quý (chi phí − cố định × ngày/365) / doanh thu |
| Giả định theo năm (λ = 0, `annual_only`) | Thuế suất hiệu dụng, cổ tức, capex/doanh thu, capex, tỷ lệ khấu hao, tỷ lệ chuyển XDCB, tăng trưởng dài hạn; ràng buộc tiền tối thiểu, trần nợ/tài sản, tỷ trọng vay ngắn hạn |
| Số tiền/sản lượng dạng luồng | Default năm chia theo tỷ trọng ngày của quý trong năm; giả định số dư (tiền tối thiểu, số cửa hàng/backlog đầu kỳ, công suất) giữ nguyên |
| Ràng buộc | Chỉ kiểm cuối năm (mục 6) |
| Analyst ghi đè số năm | Là mục tiêu năm: quý còn lại nhận giá trị sao cho chỉ tiêu năm tính như chỉ số năm bằng số nhập, phần chênh so với quý đã công bố dồn vào quý còn lại (`targets`: tỷ lệ trên cơ sở luồng hoặc số dư đầu năm, số ngày ở Q4, tăng trưởng dòng luồng, số tiền trừ lũy kế). Lượt đầu chạy với số nhập để lấy cơ sở quý, rồi giải và chạy lại đúng 2 vòng (không lặp đến hội tụ). Giả định không khai báo mục tiêu dùng số nhập cho mọi quý; tăng trưởng số dư so cùng kỳ khớp đúng ở Q4 |

Không có số quý (hoặc quý công bố mới nhất không liền năm gốc): chạy hoàn toàn theo năm, ghi chú `annual_ytd`.
Kịch bản gốc quý (nhãn `"2026Q2"`) không chạy (`frequency_unavailable`). Bỏ PHASE-01 (không còn phân bổ).

## 9. Kiểm tra và trạng thái

| Loại | Mã | Ý nghĩa |
|---|---|---|
| Lỗi chặn | `unbalanced` | BS lệch quá dung sai |
| Lỗi chặn | `negative_balance` | Phải thu, tồn kho, phải trả, … âm |
| Lỗi chặn | `unowned_line`, `unmapped_balance` | Dòng BS không có method ghi, mã nguồn chưa map |
| Lỗi chặn | CAL-03 | Doanh thu L1 không tính được |
| Cảnh báo | `funding_gap` | Thiếu vốn sau cân nguồn: kịch bản không khả thi |
| Cảnh báo | `negative_fcf`, `negative_gross_profit`, `negative_npat` | Tín hiệu cần xem lại, không phải lỗi. Không đặt sàn lợi nhuận hay trần tăng trưởng tùy tiện |
| Cảnh báo | REV-10, ONE-01, AUTO-02 | Như trên |
| Thông tin | ONE-02, AUTO-01, CON-01 | Công bố khoản bất thường, điều chỉnh tự động, kiểm thử L1 ≡ L2 |

Kịch bản được gắn nhãn **có thể công bố** chỉ khi: không có lỗi, BS cân, tiền khớp, và khả thi (không funding gap).

**Khóa bản chốt.** `tests/test_l1_frozen.py` tính dấu vân tay của toàn bộ phần L1 (method L1, giả định L1 và
default, `default_paths`, `one_off`, tần suất, `quarterly_engine`). Sửa bất kỳ phần nào làm test đỏ: muốn đổi L1 phải có owner duyệt, cập nhật
tài liệu này (tăng phiên bản) rồi mới cập nhật dấu vân tay.

## 10. Giải thích ô và dải kịch bản

- **Giải thích ô** (`quick_model/explain.py`): bấm một ô (dự phóng hoặc thực tế) để xem công thức, giá trị từng
  yếu tố ở kỳ so sánh và kỳ này, đóng góp của từng yếu tố (cộng lại bằng thay đổi), nguồn giả định, xem tiếp dòng
  thành phần, bối cảnh lũy kế quý. Ô thực tế dùng đúng công thức của method đang chọn, yếu tố đọc lại từ BCTC;
  phần không khớp hiện thành mục "Khác". Khoản bất thường đã xác nhận là một bước riêng, kể cả phần đảo lại ở năm sau.
- **Dải kịch bản** (`quick_model/engine/bands.py`): thận trọng / lạc quan = kết quả cơ sở đã xác nhận × 0,9 / × 1,1
  (số âm: ± 10% giá trị tuyệt đối). Không đổi giả định, không chạy lại mô hình, nên dải là khoảng tham chiếu, không
  phải một bộ BCTC cân đối riêng. Biên gộp giữ như cơ sở.

## 11. Giản lược và hạn chế đã biết

1. Phải trả tính theo giá vốn, không theo giá trị mua hàng.
2. Không tách XDCB: capex ghi thẳng vào TSCĐ, dở dang dài hạn giữ nguyên; capex tiền = tăng TSCĐ.
3. Lãi vay trên số dư đầu kỳ (không lặp). DT tài chính theo mức lịch sử (v2.5): không phản ứng với thay đổi số dư tiền
   dự phóng (analyst ghi đè khi DN tích/rút tiền mạnh).
4. Thuế đã nộp = thuế kỳ; không mô hình hóa thuế hoãn lại, chuyển lỗ.
5. Lỗ thì thuế = 0 (không ghi nhận lợi ích thuế).
6. Phần lớn dòng BS ngoài vốn lưu động, TSCĐ, nợ vay, vốn chủ giữ nguyên số dư.
7. Cổ tức, phát hành, lợi nhuận khác mặc định 0; LDLK theo mức lịch sử (v2.6).
8. Khoản bất thường coi là tiền; chỉ tách ở dòng ngoài hoạt động lõi; quét không tuần tự nên một khoản rất lớn làm
   phình σ của 12 quý sau và có thể che khoản nhỏ hơn.
9. Năm thiếu báo cáo FY chưa dùng được làm năm gốc.
10. Lịch sử không phải point-in-time: không dựng lại được số liệu theo từng thời điểm công bố.
11. Hồ sơ `demo` dùng dữ liệu HPG cho các ngành khác chỉ để thử phương pháp, không phải BCTC thật của doanh nghiệp ngành đó.
12. Quý: cổ tức theo tỷ lệ chi trả trên LNST từng quý (không đặt vào quý chi thực tế); thuế suất và tiền tối thiểu theo
    năm; tiền giữa năm có thể về 0 khi vay tự động chỉ chạy để giữ tiền không âm.
13. Default quý theo tăng trưởng so cùng kỳ: quý có gốc năm trước thấp/cao bất thường cho tăng giảm mạnh so quý liền
    trước (vd HPG 2026Q3).

## 12. Lịch sử quyết định

| Ngày | Quyết định |
|---|---|
| 2026-11-12 | Có cả engine năm và engine quý; mặc định theo năm (thay bằng quyết định 2026-11-18) |
| 2026-11-16 | Bốn nhóm BCTC; L1 chung cho cả nhóm phi tài chính; cân nguồn tự động L1 (đích trung điểm); lãi suất và suất sinh lời = thực tế kỳ liền trước; kỳ gốc lấy từ dữ liệu; khoản bất thường; tài liệu mô hình bắt buộc |
| 2026-11-17 | Đường default hội tụ thay bản giữ nguyên một giá trị; mức neo EMA 5 năm; tăng trưởng dài hạn min(EMA, 8%) đạt ở năm thứ 5; mùa vụ theo quý; CPBH/CPQL cố định + biến đổi; giải thích ô thực tế; dải kịch bản = cơ sở × 0,9 / × 1,1; duyệt mô hình HPG L1; **chốt L1 phi tài chính v1.0** |
| 2026-11-18 | Số năm phải giống nhau dù xem theo quý hay năm: bỏ engine quý, xem theo quý = phân bổ số năm theo mùa vụ (phương án A); bỏ mùa vụ trong default; thêm PHASE-01; **L1 v1.1** |
| 2026-11-18 | Phân bổ không bắt được đà ngắn hạn: phương án B — engine quý đến hết Q4 năm sau rồi năm, default quý = 0,65 × EMA 8 quý liên tiếp + 0,35 × năm (năm giữ nguyên), không tách theo nhóm dòng; λ = 0 cho thuế, cổ tức, capex, khấu hao, chuyển XDCB; ràng buộc tiền tối thiểu, trần nợ chỉ kiểm cuối năm; analyst ghi đè năm = mục tiêu năm, dồn phần chênh vào quý còn lại; tự làm sạch khoản bất thường không đợi xác nhận (quét không tuần tự); bỏ phân bổ và PHASE-01; **L1 v2.0** |
| 2026-11-18 | Chuỗi chưa đủ độ dài cho EMA (năm < 5, quý < 8) dùng số kỳ liền trước, hội tụ/giảm dần giữ nguyên; **L1 v2.1** |
| 2026-11-18 | Doanh thu quý chưa bắt mức mới khi mở rộng công suất (HPG DQ2): phương án E — thêm QoQ mùa vụ bên cạnh YoY và năm (0,50 / 0,15 / 0,35), YoY và năm đo trên nền điều chỉnh mức 2 quý; **L1 v2.2** |
| 2026-11-19 | Thiên lệch Q4 (backtest): giữ trọng số doanh thu 0,50/0,15/0,35; thử trung vị mùa vụ 3 năm, co biên độ α 0,5/0,6, co chỉ ở QoQ; chọn co biên độ mùa vụ α = 0,6 cho cả QoQ và quy mức năm; **L1 v2.4** |
| 2026-11-18 | Sau backtest 272 mã (owner): default ngoài miền hợp lệ chỉ cảnh báo (`default_out_of_range`), số analyst nhập ngoài miền vẫn chặn; tỷ lệ chi phí tài chính ngoài lãi vay được âm; không có nợ vay trong lịch sử thì lãi suất default 0 và vay tự động là ngắn hạn (`if_no_history`), phát sinh nợ thì cảnh báo INT-01; doanh thu quý ≤ 1,5 × bình quân 4 quý × 4 × tỷ trọng mùa vụ; **L1 v2.3** |
| 2026-11-19 | Nghiên cứu sai số (so với dự báo ngây thơ): EMA quý của tỷ lệ có đuôi lỗi lớn (biên gộp: sai số TB 9,5 điểm % so với 5,0 khi EMA có trọng số); biên gộp quý TTM + 0,25 × (quý gần nhất − TTM) tốt nhất; DT tài chính theo mức (trung vị 4 quý MedAPE 36,5% so với 46,1% của suất sinh lời × số dư). Owner duyệt A (EMA có trọng số), B (biên gộp quý), C (DT tài chính theo mức; thay `financial_income_yield` bằng `financial_income_run_rate`); **L1 v2.5** |
| 2026-11-19 | Backtest v2.5: DT tài chính bias âm (năm 0 −24%). Thử bình quân 4 quý (engine: năm 0 bias −15%), xu hướng giá trị tuyệt đối (OLS, Theil-Sen giảm dần: bias gần 0 nhưng MedAPE kém hơn 4–6 điểm), 13 cách xử lý outlier. Owner: luồng báo cáo tự động không chờ analyst duyệt nên phải tự loại outlier (HPG 2026Q1 gấp ~10 lần). Chọn bình quân 4 quý + quy tắc đột biến cô lập (5× trung vị 8 quý, quý sau về ≤ 2×) + trần 5× cho quý mới nhất cho DT tài chính (thử ngoài engine: năm 0 MedAPE 37,2%, bias −12,7%, so với làm sạch 3σ 41,0%, −21,6%); **L1 v2.5.1** |
| 2026-11-19 | So với dự phóng ngây thơ (YTD + cùng quý năm trước): ngân hàng tốt hơn mọi thời điểm; phi tài chính kém hơn ở đầu năm, sau Q1 (LNST mẹ MedAPE 46% so với 36%, gộp nhóm 40% so với 25%), ngang sau Q2–Q3; đổi riêng biên gộp (TTM, 3 năm, cùng quý năm trước) không cải thiện; trộn với cùng quý năm trước theo w = 0,75 × số quý còn lại / 4 cải thiện mọi thời điểm (khung nghiên cứu: LNST đầu năm 36% / 53% / 28%); LDLK theo năm trước giảm đóng góp sai số LNST 8,8 → 4,0 điểm %. Owner duyệt neo năm trước (năm +1, +2 giảm dần) và LDLK theo mức; **L1 v2.6** |
| 2026-11-20 | Backtest v2.6: dự phóng 1 quý tới xấu đi theo quy mô (LNST WAPE 65% → 75%) vì quý đầu bị neo w = 0,75. Thử (a) neo doanh thu về tăng trưởng xu hướng `long_run_growth` (giảm lệch thấp nhưng năm +1 lệch +15%), (b) w theo số quý tới (1 quý tới WAPE 75% → 68%, gộp nhóm 43% → 38%; các kỳ khác gần như giữ nguyên), (a)+(b). Owner chốt (b); không lấy (a); **L1 v2.6.1** |
| 2026-11-20 | Nghiên cứu dự phóng 1 quý tới (không BĐS): mô hình tốt hơn dự phóng ngây thơ ở hầu hết dòng, trừ chi phí lãi vay (WAPE 46% so với 20% của "bằng quý trước"). Nguyên nhân: lãi suất = lãi vay / dư nợ đầu kỳ; khi dư nợ đầu kỳ gần 0 (vd CTD 2022) lãi suất ~4.590%/năm, kéo theo vòng vay tự động → lãi vay hàng nghìn tỷ. Đổi sang dư nợ bình quân (đầu + cuối kỳ) / 2 cho default năm và chuỗi quý: lãi vay 1 quý tới WAPE 43% → 22%, ca LNST lệch > vốn chủ 74 → 34, LNST 1 quý tới WAPE 53% → 48%. Owner duyệt; **L1 v2.6.2** |
| 2026-11-20 | Nghiên cứu các ca LNST lệch > vốn chủ còn lại ở v2.6.2 (34 dự phóng 1 quý tới / 13 mã, không BĐS): (1) tỷ lệ CĐKKS/LNST quý theo EMA bị phóng đại khi LNST lịch sử gần 0 (HU1, VID, SMC, FCN); đổi sang tổng CĐKKS 4 quý / tổng LNST 4 quý: CĐKKS 1 quý tới WAPE 68% → 47%; (2) biên gộp lấy từ quý doanh thu gần 0 qua phần quán tính và neo (BTP, DAH, TNT, TFC): quán tính và neo theo trọng số doanh thu. Gộp cả ba: ca bùng nổ 34 → 15 (9 mã), LNST 1 quý tới WAPE (gồm ca bùng nổ) 51% → 47%, bỏ ca bùng nổ gần như không đổi. LNST Q4 lệch hơn do trích lập/quyết toán cuối năm; dùng tỷ lệ Q4 năm trước kém hơn (WAPE 56% → 58%) nên không sửa, chỉ công bố. Owner duyệt; **L1 v2.6.3** |
| 2026-11-18 | Owner giữ trọng số 0,50 / 0,15 / 0,35 và **chốt L1 v2.2** (cùng các quyết định ngân hàng trong ngày: AEY/COF chỉ theo quý, lịch sử ngắn dùng số kỳ liền trước) |
| 2026-11-26 | Owner chốt **L1 v2.6.6**: lãi suất vay quý = lãi suất quý gần nhất. Backtest 120 DN (mẫu): lãi vay WAPE Q+1 20,1% → 14,5%, Q+2 24,6% → 21,0%, năm tới 20,3% → 17,8%; LNST WAPE giảm 0,3–0,7 điểm. Đã thử và không chọn: lãi vay trên dư nợ bình quân (hai lượt, kết quả trùng bản gốc), DT tài chính = suất sinh lợi × số dư bình quân (không tốt hơn bình quân 4 quý kể cả với số dư thực tế) |

---
Tài liệu nội bộ; cần rà soát và kiểm chứng trước khi dùng ngoài bộ phận hoặc ban hành.
