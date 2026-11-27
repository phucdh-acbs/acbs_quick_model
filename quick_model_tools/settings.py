"""Cấu hình của hai công cụ (một chỗ). Số của mô hình nằm trong quick_model/config, không lặp lại ở đây."""

# Nhóm báo cáo tài chính suy từ COM_TYPE_CODE và ICB_CODE của dòng ORGANIZATION đang hiệu lực, không đoán từ mã khoản mục. Khi mô hình có thêm nhóm:
# thêm vào `family_by_com_type` (hoặc bỏ tiền tố ICB), không phải sửa mã.
ROUTING = {
    "family_by_com_type": {"CT": "nonfinancial", "NH": "bank"},
    "unsupported_com_type": {"CK": "chứng khoán", "BH": "bảo hiểm"},
    "unsupported_icb_prefix": {"86": "bất động sản"},
    "waiting": "mô hình chưa có nhóm này, đang chờ bản cập nhật; tạm dùng luồng sẵn có",
}

# Quy tắc của mô hình từ v2.6.8 (docs/HUONG_DAN_NHANH_AGENT.md): dùng kết quả khi `status.usable = true`. Thiếu vốn, LNST âm, bảng cân đối lệch là
# cờ đi kèm, không chặn; `publishable` chỉ quyết định đăng tự động. Chỉ chặn khi mô hình không dùng được (lỗi chặn trong issues).
GATE = {'block_texts': {'NO_RESULT': 'mô hình có lỗi chặn, không có số: {detail}',
                 'NEEDS_INPUT': 'mô hình cần nhập giả định vì lịch sử không có mẫu hợp lệ: {ids}; truyền overrides {{mã giả định: số}} kèm lý do'},
 'flag_texts': {'FUNDING_GAP': 'thiếu vốn tối đa {gap} tỷ đồng từ {period} (kịch bản không có vốn bổ sung); vẫn dùng số',
                'NEGATIVE_EARNINGS': 'LNST mẹ âm ở {what}: không nhân P/E với LNST âm; vẫn dùng số',
                'NOT_BALANCED': 'bảng cân đối dự phóng lệch, xem issues',
                'NOT_PUBLISHABLE': 'mô hình chưa cho đăng tự động (publishable = false); vẫn dùng số',
                'LATEST_QUARTER_NOT_USED': 'quý {raw} có trong dữ liệu nhưng bị bỏ, mô hình chỉ dùng đến {used}',
                'SCOPE_SUSPECT': 'nghi đổi phạm vi hợp nhất từ {q} ({what}): các năm sau {y} theo cơ cấu cũ; có thể chạy lại với rebase, chuyên viên nên xem',
                'REBASED': 'đã đặt lại giả định theo các quý từ {q} vì đổi phạm vi hợp nhất ({what}); tăng trưởng doanh thu vẫn là của mô hình; chuyên viên nên xem',
                'OVERRIDE_OUTSIDE_HISTORY': 'giả định đặt tay nằm ngoài khoảng lịch sử: {ids}'},
 'warn_codes_override': ('outside_history', 'high_growth'),
 'scope': {'min_before': 4, 'max_after': 4, 'minority_min_share': 0.05, 'minority_ratio': 0.25, 'jv_min_share': 0.1, 'jv_ratio': 3.0}}

BRIEF_YEARS = 2
HEADLINE = "npat_parent"
KEY_LINES = {'nonfinancial': ('net_revenue', 'gross_profit', 'pbt', 'npat_parent'), 'bank': ('nii', 'toi', 'provision_expense', 'npat_parent')}

VALUATION = {'rule_status': 'luật tạm thời, chưa phải công thức cuối cùng Phòng Phân tích chốt',
 'window_years': 5,
 'min_days': 250,
 'stale_days': 10,
 'stats': (('p10', '0.10'), ('p25', '0.25'), ('p50', '0.50'), ('p75', '0.75'), ('p90', '0.90')),
 'pb_roe_years': 3,
 'eps_growth_min': '0.20',
 'beta_trial': ('0.8', '1.0', '1.2'),
 'g_trial': ('0.02', '0.04'),
 'beta_range': ('0.3', '2.5'),
 'g_range': ('-0.02', '0.05'),
 'rerating_flag': '0.1',
 'associates_flag': 0.15,
 'typical_npat_error': {'nonfinancial': '0.31',
                        'bank': '0.16',
                        'basis': 'sai số trung vị LNST cả năm, backtest v2.6.3 (docs/phuong_phap/quy_trinh_L1.md)'},
 'unavailable': {'bank': {'ev_ebitda': 'không áp dụng cho ngân hàng',
                          'dcf_fcff': 'không áp dụng cho ngân hàng',
                          'dcf_fcfe': 'không áp dụng cho ngân hàng',
                          'ddm': 'mô hình không dự phóng cổ tức'},
                 'nonfinancial': {'ev_ebitda': 'chưa có lịch sử EV/EBITDA đo được',
                                  'dcf_fcff': 'dòng tiền của mô hình chưa khớp định nghĩa định giá (capex, vốn lưu động)',
                                  'dcf_fcfe': 'dòng tiền của mô hình chưa khớp định nghĩa định giá (capex, vốn lưu động)',
                                  'ddm': 'mô hình không dự phóng cổ tức',
                                  'sotp': 'chưa có bội số đo được cho phần công ty liên kết: cần chuyên viên'}}}
