"""Markdown 报告生成：每个结论都带来源、区间与数据完整性说明，
不输出无来源的孤立数值。
"""
from __future__ import annotations

from ..schemas import AnalysisResult


def render_markdown(result: AnalysisResult, specimen_code: str,
                    run_meta: dict | None = None) -> str:
    fit = result.elastic_fit
    yld = result.yield_result
    frac = result.fracture
    prov = result.provenance

    lines: list[str] = []
    lines.append(f"# 拉伸试验分析报告 — 试样 {specimen_code}（试验记录 #{result.run_id}）")
    lines.append("")
    if run_meta:
        lines.append(f"- 设备：{run_meta.get('machine') or '未记录'}")
        lines.append(f"- 采样率：{run_meta.get('sampling_rate_hz') or '未记录'} Hz")
    lines.append(f"- 应变来源：**{prov['strain_source']}**（{prov['strain_basis']}）")
    lines.append(f"- 初始截面积 A0 = {prov['initial_area_m2']:.6e} m²")
    lines.append(f"- 应力输出单位：{result.stress_unit}")
    comp = result.compliance_correction
    if comp.enabled:
        lines.append(
            f"- 机器柔度修正：**已应用**，C = {comp.coefficient:.6g} {comp.unit}"
            f"（= {comp.coefficient_m_per_n:.6g} m/N）"
        )
        lines.append(f"- 修正规则：{comp.machine_displacement_rule}")
        lines.append("- 原始夹具/引伸计通道未回写；修正曲线为独立派生曲线，用于本次拟合与屈服计算")
    else:
        lines.append("- 机器柔度修正：未启用，本次拟合、屈服和断裂指标均基于未修正通道")
    lines.append("")

    lines.append("## 1. 弹性模量 E")
    lines.append(
        f"- **E = {fit.modulus_in_output_unit:.4g} {fit.modulus_unit}**"
        f"（= {fit.slope_pa:.6g} Pa）"
    )
    lines.append(
        f"- 拟合方法：{prov['fit_method']}；"
        f"弹性区间应变 [{fit.strain_min:.6g}, {fit.strain_max:.6g}]，"
        f"参与点 {fit.n_points} 个，人工排除 {fit.n_excluded} 个"
    )
    lines.append(f"- 拟合优度 R² = {fit.r_squared:.6f}；RMSE = {fit.rmse_pa:.4g} Pa；"
                 f"最大残差绝对值 = {fit.max_abs_residual_pa:.4g} Pa")
    if fit.excluded:
        lines.append("- 人工排除点（含原因）：")
        for p in fit.excluded:
            lines.append(f"  - 索引 {p.index}：{p.reason}")
    lines.append("- 区间影响（区间选择对模量的影响）：")
    for s in result.interval_sensitivity:
        lines.append(
            f"  - {s.label}：应变 [{s.strain_min:.6g}, {s.strain_max:.6g}]，"
            f"E = {s.modulus_in_output_unit:.4g} {fit.modulus_unit}，"
            f"R² = {s.r_squared:.6f}，点数 {s.n_points}"
        )
    lines.append("")

    lines.append("## 2. 屈服强度（0.2% 偏移法）")
    lines.append("- 偏移线 σ = E·(ε − 0.002)，应变单位 mm/mm（0.2% 为无量纲 0.002）")
    if yld.found:
        unclear = "；⚠ 无明显屈服平台，仅条件屈服值" if yld.yield_unclear else ""
        lines.append(
            f"- **Rp0.2 = {yld.proof_stress:.4g} {yld.stress_unit}**"
            f"，交点处应变 {yld.at_strain:.6g}{unclear}"
        )
    else:
        lines.append(f"- **未确定**：{yld.reason}")
    lines.append(f"- 判定依据：{yld.reason}")
    lines.append("")

    lines.append("## 3. 强度与断后指标")
    lines.append(f"- 抗拉强度 Rm = **{frac.ultimate_tensile_strength:.4g} "
                 f"{frac.stress_unit}**（UTS 处工程应变 {frac.strain_at_uts:.6g}）")
    if frac.engineering_fracture_strain is not None:
        lines.append(f"- 断裂点工程应变 = {frac.engineering_fracture_strain:.6g}")
    lines.append(f"- 断后伸长率 A = "
                 + (f"**{frac.elongation_percent:.3f} %**" if frac.elongation_percent is not None
                    else "未计算")
                 + ("" if frac.elongation_percent is not None
                    else "（缺断后标距 Lu）"))
    lines.append(f"- 断面收缩率 Z = "
                 + (f"**{frac.reduction_of_area_percent:.3f} %**" if frac.reduction_of_area_percent is not None
                    else "未计算")
                 + ("" if frac.reduction_of_area_percent is not None
                    else "（缺断后直径 du 或截面信息）"))
    if frac.missing_inputs:
        lines.append("- 缺失输入（指标因此留空，未做隐式假设）：")
        for m in frac.missing_inputs:
            lines.append(f"  - {m}")
    lines.append("")

    lines.append("## 4. 真实应力-应变与颈缩")
    if result.necking_index is not None:
        lines.append(f"- 颈缩起点：曲线第 {result.necking_index} 点（载荷峰值）。"
                     "其后 σ_true=σ_eng(1+ε)、ε_true=ln(1+ε) 的简单换算失效，"
                     "曲线上标记为缺失，不参与任何后续计算。")
    lines.append("")

    lines.append("## 5. 数据溯源")
    for k, v in prov.items():
        if k == "excluded_points" and not v:
            continue
        lines.append(f"- {k}: {v}")
    if result.warnings:
        lines.append("")
        lines.append("## 6. 警告")
        for w in result.warnings:
            lines.append(f"- {w}")
    return "\n".join(lines)
