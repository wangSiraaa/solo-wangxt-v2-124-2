import {
  AfterViewInit, Component, ElementRef, OnDestroy, ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import Plotly from 'plotly.js-dist-min';

import { ApiService } from '../api.service';
import {
  AnalysisResult, ExcludedPoint, FitRequest, MachineComplianceCorrection, Report, RunDetail,
} from '../models';

type DemoCase = 'linear_elastic' | 'no_clear_yield' | 'clear_yield';

@Component({
  selector: 'app-curve-viewer',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './curve-viewer.component.html',
  styleUrls: ['./curve-viewer.component.css'],
})
export class CurveViewerComponent implements AfterViewInit, OnDestroy {
  @ViewChild('plot') plotEl!: ElementRef<HTMLDivElement>;
  @ViewChild('residualPlot') residualEl!: ElementRef<HTMLDivElement>;

  demoCase: DemoCase = 'linear_elastic';
  omitDiameter = false;
  run: RunDetail | null = null;
  result: AnalysisResult | null = null;
  report: Report | null = null;

  strainSource: 'extensometer' | 'crosshead' = 'extensometer';
  stressUnit = 'MPa';
  complianceEnabled = false;
  complianceCoefficient: number | null = 6.11155;
  complianceUnit = 'mm/kN';
  compliancePreviewInvalid = false;
  compliancePreviewReason = '';
  strainMin: number | null = null;
  strainMax: number | null = null;
  excluded: ExcludedPoint[] = [];
  excludeReason = '';
  pendingExcludeIndex: number | null = null;

  loading = false;
  error = '';

  private plotlyReady = false;

  constructor(private api: ApiService) {}

  ngAfterViewInit(): void {
    Plotly.newPlot(this.plotEl.nativeElement, [], {
      title: '应力-应变曲线（框选弹性区间，单击点可排除）',
      xaxis: { title: '工程应变 ε (mm/mm)' },
      yaxis: { title: '应力 (MPa)' },
      dragmode: 'select',
      selectdirection: 'h',
      hovermode: 'closest',
    }, { responsive: true, displaylogo: false });

    const el = this.plotEl.nativeElement as unknown as PlotlyDiv;

    // 框选 -> 设定弹性区间（取所选点的应变为界，所见即所得）
    el.on('plotly_selected', (event: PlotlySelected) => {
      const pts = event?.points ?? [];
      if (!pts.length) { return; }
      const xs = pts.map((p) => p.x as number);
      this.strainMin = Math.min(...xs);
      this.strainMax = Math.max(...xs);
    });

    // 单击曲线点 -> 登记待排除点，必须在输入框写明原因
    el.on('plotly_click', (event: PlotlyClick) => {
      const p = event?.points?.[0];
      if (p && p.curveNumber === 0) {
        this.pendingExcludeIndex = p.pointNumber;
      }
    });
    this.plotlyReady = true;
  }

  ngOnDestroy(): void {
    if (this.plotlyReady) {
      Plotly.purge(this.plotEl.nativeElement);
    }
  }

  loadDemo(): void {
    this.error = '';
    this.loading = true;
    this.result = null;
    this.report = null;
    this.excluded = [];
    this.api.createDemo(this.demoCase, this.omitDiameter).subscribe({
      next: (d) => {
        this.api.getRun(d.run_id).subscribe((run) => {
          this.run = run;
          this.strainSource = run.calc_plan.strain_source as 'extensometer' | 'crosshead';
          this.stressUnit = run.calc_plan.stress_unit;
          this.strainMin = null;
          this.strainMax = null;
          this.complianceEnabled = false;
          this.complianceCoefficient = 6.11155;
          this.complianceUnit = 'mm/kN';
          this.loading = false;
          this.drawRawCurve();
        });
      },
      error: (e) => { this.error = this.formatHttpError(e); this.loading = false; },
    });
  }

  confirmExclude(): void {
    if (this.pendingExcludeIndex === null) { return; }
    const reason = this.excludeReason.trim();
    if (!reason) {
      this.error = '人工排除点必须填写原因';
      return;
    }
    if (!this.excluded.some((p) => p.index === this.pendingExcludeIndex)) {
      this.excluded.push({ index: this.pendingExcludeIndex!, reason });
    }
    this.pendingExcludeIndex = null;
    this.excludeReason = '';
    this.error = '';
    this.drawRawCurve();
  }

  cancelExclude(): void {
    this.pendingExcludeIndex = null;
    this.excludeReason = '';
  }

  removeExcluded(i: number): void {
    this.excluded.splice(i, 1);
    this.drawRawCurve();
  }

  onStrainSourceChange(): void {
    if (this.strainSource === 'extensometer') {
      this.complianceEnabled = false;
    }
    this.drawRawCurve();
  }

  onComplianceChange(): void {
    this.drawRawCurve();
  }

  private compliancePayload(): MachineComplianceCorrection | null {
    if (!this.complianceEnabled || this.strainSource !== 'crosshead') {
      return null;
    }
    return {
      enabled: true,
      coefficient: this.complianceCoefficient,
      unit: this.complianceUnit,
    };
  }

  private validateComplianceForRequest(): string {
    if (!this.complianceEnabled || this.strainSource !== 'crosshead') {
      return '';
    }
    if (this.complianceCoefficient === null || Number.isNaN(this.complianceCoefficient)) {
      return '已启用机器柔度修正，但缺少柔度系数；请输入校准值或关闭修正';
    }
    if (this.complianceCoefficient <= 0) {
      return '机器柔度系数必须大于 0';
    }
    if (!this.complianceUnit) {
      return '已启用机器柔度修正，但缺少校准单位；请选择 m/N、mm/N、mm/kN 或 µm/N';
    }
    return '';
  }

  private complianceMPerN(): number {
    const factors: Record<string, number> = {
      'm/N': 1, 'mm/N': 1e-3, 'mm/kN': 1e-6, 'µm/N': 1e-6,
    };
    return Number(this.complianceCoefficient ?? 0) * (factors[this.complianceUnit] ?? 0);
  }

  private checkCorrectedPreview(): { valid: boolean; reason: string } {
    if (!this.run || !this.complianceEnabled || this.strainSource !== 'crosshead') {
      return { valid: true, reason: '' };
    }
    const rawDisp = this.run.raw_values['crosshead_displacement'];
    const rawLoad = this.run.raw_values['load'];
    const dispUnit = this.run.channels.find((c) => c.kind === 'crosshead_displacement')?.unit ?? 'm';
    const loadUnit = this.run.channels.find((c) => c.kind === 'load')?.unit ?? 'N';
    if (!rawDisp || !rawLoad || !rawDisp.length) {
      return { valid: false, reason: '缺少夹具位移通道，无法进行柔度修正预览' };
    }
    const dispFactor = dispUnit === 'mm' ? 1e-3 : 1;
    const loadFactor = loadUnit === 'kN' ? 1e3 : 1;
    const c = this.complianceMPerN();
    if (!c || this.complianceCoefficient === null || this.complianceCoefficient <= 0) {
      return { valid: false, reason: this.validateComplianceForRequest() || '柔度系数无效' };
    }
    const firstDisp = rawDisp[0] * dispFactor;
    const firstLoad = rawLoad[0] * loadFactor;
    const tolerance = 1e-12 * Math.max(...rawDisp.map((v) => Math.abs(v * dispFactor)));
    for (let i = 0; i < rawDisp.length; i++) {
      const deltaDisp = rawDisp[i] * dispFactor - firstDisp;
      const deltaLoad = rawLoad[i] * loadFactor - firstLoad;
      const corrected = deltaDisp - c * deltaLoad;
      if (corrected < -tolerance) {
        return {
          valid: false,
          reason: `第 ${i} 点修正后位移为 ${corrected.toExponential(3)} m（<0）：柔度过大或单位错误，已阻止提交`,
        };
      }
      if (i > 0) {
        const prevDisp = rawDisp[i - 1] * dispFactor - firstDisp;
        const prevLoad = rawLoad[i - 1] * loadFactor - firstLoad;
        if (deltaLoad - prevLoad > 0 && corrected - (prevDisp - c * prevLoad) < -tolerance) {
          return {
            valid: false,
            reason: `第 ${i} 点载荷增加但修正后位移减少，校准值或单位可能错误，已阻止提交`,
          };
        }
      }
    }
    return { valid: true, reason: '' };
  }

  private buildRequest(): FitRequest {
    return {
      run_id: this.run!.run_id,
      strain_source: this.strainSource,
      stress_unit: this.stressUnit,
      strain_min: this.strainMin,
      strain_max: this.strainMax,
      excluded_points: this.excluded,
      machine_compliance: this.compliancePayload(),
    };
  }

  private formatHttpError(e: { error?: unknown }): string {
    const err = e.error;
    if (typeof err === 'object' && err !== null) {
      const detail = (err as { detail?: unknown }).detail;
      if (typeof detail === 'string') { return detail; }
      if (Array.isArray(detail)) {
        return detail.map((d) =>
          (d as { msg?: string }).msg ?? JSON.stringify(d)).join('；');
      }
    }
    return JSON.stringify(err ?? '请求失败');
  }

  analyze(): void {
    const complianceError = this.validateComplianceForRequest();
    if (complianceError) {
      this.error = complianceError;
      return;
    }
    this.error = '';
    this.loading = true;
    this.report = null;
    this.api.analyze(this.buildRequest()).subscribe({
      next: (r) => {
        this.result = r;
        this.loading = false;
        this.drawResult(r);
      },
      error: (e) => {
        this.loading = false;
        this.error = this.formatHttpError(e);
      },
    });
  }

  generateReport(): void {
    if (!this.run) { return; }
    const complianceError = this.validateComplianceForRequest();
    if (complianceError) {
      this.error = complianceError;
      return;
    }
    this.loading = true;
    this.api.report(this.run.run_id, this.buildRequest()).subscribe({
      next: (r) => { this.report = r; this.loading = false; },
      error: (e) => {
        this.loading = false;
        this.error = this.formatHttpError(e);
      },
    });
  }

  // ---- 绘图 ----

  private drawRawCurve(): void {
    if (!this.run) { return; }
    const raw = this.run.raw_values;
    const sourceKind = this.strainSource === 'extensometer'
      ? 'extensometer_displacement'
      : 'crosshead_displacement';
    const rawDisp = raw[sourceKind] ?? raw['extensometer_displacement'] ?? raw['crosshead_displacement'];
    const rawLoad = raw['load'];
    if (!rawDisp || !rawDisp.length || !rawLoad || !rawLoad.length) { return; }
    const channelUnit = this.run.channels.find((c) => c.kind === sourceKind)?.unit ?? 'm';
    const loadUnit = this.run.channels.find((c) => c.kind === 'load')?.unit ?? 'N';
    const dispFactor = channelUnit === 'mm' ? 1e-3 : 1;
    const loadFactor = loadUnit === 'kN' ? 1e3 : 1;
    const firstDisp = rawDisp[0] * dispFactor;
    const firstLoad = rawLoad[0] * loadFactor;
    const originalX = rawDisp.map((v) => v * dispFactor - firstDisp);
    const originalY = rawLoad.map((v) => v * loadFactor);

    const traces: Record<string, unknown>[] = [{
      x: originalX,
      y: originalY,
      mode: 'lines',
      name: sourceKind === 'crosshead_displacement'
        ? '原始夹具位移（未改变）'
        : '原始引伸计位移（未改变）',
      line: { color: '#64748b', dash: 'dash' },
    }];

    const preview = this.checkCorrectedPreview();
    this.compliancePreviewInvalid = !preview.valid;
    this.compliancePreviewReason = preview.reason;
    if (this.complianceEnabled && this.strainSource === 'crosshead') {
      const c = this.complianceMPerN();
      const corrected = rawDisp.map((d, i) =>
        d * dispFactor - firstDisp - c * (rawLoad[i] * loadFactor - firstLoad));
      traces.push({
        x: corrected,
        y: originalY,
        mode: 'lines',
        name: `修正夹具位移（C=${this.complianceCoefficient} ${this.complianceUnit}）`,
        line: { color: preview.valid ? '#2563eb' : '#dc2626' },
      });
    }

    Plotly.react(this.plotEl.nativeElement, traces, {
      title: '原始信号与可选机器柔度修正预览（通道分列，原始数据不回写）',
      xaxis: { title: '相对位移 (m)' },
      yaxis: { title: `载荷 (${loadUnit})` },
      dragmode: 'select',
      selectdirection: 'h',
      shapes: [],
    }, { responsive: true, displaylogo: false });
  }

  private drawResult(r: AnalysisResult): void {
    const pts = r.curve;
    const excludedSet = new Set(r.elastic_fit.excluded.map((p) => p.index));
    const engX = pts.map((p) => p.strain);
    const engY = pts.map((p) =>
      excludedSet.has(p.index) ? null : p.engineering_stress);
    const trueX = pts.filter((p) => p.true_stress_valid).map((p) => p.strain);
    const trueY = pts.filter((p) => p.true_stress_valid).map((p) => p.true_stress);
    const exclX = pts.filter((p) => excludedSet.has(p.index)).map((p) => p.strain);
    const exclY = pts.filter((p) => excludedSet.has(p.index)).map((p) => p.engineering_stress);

    // 拟合直线 + 0.2% 偏移线（应变单位 mm/mm，显式 0.002）
    const fit = r.elastic_fit;
    const Eunit = fit.modulus_in_output_unit;
    const x0 = Math.max(0, fit.strain_min - (fit.strain_max - fit.strain_min));
    const x1 = fit.strain_max + (fit.strain_max - fit.strain_min);
    const fitLine = {
      x: [x0, x1],
      y: [Eunit * x0 + this.interceptUnit(fit), Eunit * x1 + this.interceptUnit(fit)],
      mode: 'lines' as const, name: `弹性拟合 E=${Eunit.toFixed(0)} ${fit.modulus_unit}`,
      line: { color: '#ea580c', dash: 'dash' },
    };
    const ox1 = r.curve[r.curve.length - 1].strain;
    const offsetLine = {
      x: [0.002, ox1],
      y: [0, Eunit * (ox1 - 0.002)],
      mode: 'lines' as const, name: '0.2% 偏移线 (ε₀=0.002 mm/mm)',
      line: { color: '#16a34a', dash: 'dot' },
    };
    const shapes: unknown[] = [{
      type: 'rect', xref: 'x', yref: 'paper',
      x0: fit.strain_min, x1: fit.strain_max, y0: 0, y1: 1,
      fillcolor: 'rgba(37,99,235,0.08)', line: { width: 0 },
      layer: 'below',
    }];
    if (r.yield_result.found && r.yield_result.at_strain !== null) {
      shapes.push({
        type: 'line', xref: 'x', yref: 'paper',
        x0: r.yield_result.at_strain, x1: r.yield_result.at_strain,
        y0: 0, y1: 1, line: { color: '#16a34a', dash: 'dash', width: 1 },
      });
    }
    if (r.necking_index !== null) {
      shapes.push({
        type: 'line', xref: 'x', yref: 'paper',
        x0: pts[r.necking_index].strain, x1: pts[r.necking_index].strain,
        y0: 0, y1: 1, line: { color: '#b91c1c', dash: 'dot', width: 1 },
      });
    }

    const activeName = r.compliance_correction.applied ? '修正后工程应力-应变（参与拟合）' : '工程应力-应变';
    const traces: Record<string, unknown>[] = [
      { x: engX, y: engY, mode: 'lines', name: activeName,
        line: { color: '#2563eb' } },
    ];
    if (r.compliance_correction.applied) {
      traces.push({
        x: pts.map((p) => p.uncorrected_strain),
        y: pts.map((p) => p.engineering_stress),
        mode: 'lines', name: '原始夹具位移应变（仅对比，不参与拟合）',
        line: { color: '#94a3b8', dash: 'dash' },
      });
    }
    traces.push(
      { x: trueX, y: trueY, mode: 'lines', name: '真实应力（颈缩后不换算）',
        line: { color: '#7c3aed' } },
      { x: exclX, y: exclY, mode: 'markers', name: '人工排除点（有原因）',
        marker: { color: '#dc2626', size: 8, symbol: 'x' } },
      fitLine, offsetLine,
    );

    Plotly.react(this.plotEl.nativeElement, traces, {
      title: `应力-应变（应变来源：${r.strain_source_used}${
        r.compliance_correction.applied ? '，已扣除机器柔度' : ''
      }）— 红竖线颈缩起点`,
      xaxis: { title: '工程应变 ε (mm/mm)' },
      yaxis: { title: `应力 (${r.stress_unit})` },
      shapes,
      dragmode: 'select',
      selectdirection: 'h',
    }, { responsive: true, displaylogo: false });

    // 残差图：弹性区内逐点残差，让用户看到区间是否合适
    Plotly.react(this.residualEl.nativeElement, [
      {
        x: fit.residuals.map((p) => p.strain),
        y: fit.residuals.map((p) => p.residual),
        mode: 'markers+lines', name: '拟合残差',
        marker: { color: '#ea580c', size: 5 },
      },
    ], {
      title: `弹性拟合残差（R²=${fit.r_squared.toFixed(6)}，RMSE=${(fit.rmse_pa / 1e6).toFixed(3)} MPa）`,
      xaxis: { title: '应变 ε (mm/mm)' },
      yaxis: { title: `残差 (${fit.modulus_unit})` },
    }, { responsive: true, displaylogo: false });
  }

  private interceptUnit(fit: AnalysisResult['elastic_fit']): number {
    // intercept 以 Pa 存储，转到输出应力单位
    const factor: Record<string, number> = { Pa: 1, MPa: 1e-6, GPa: 1e-9 };
    return fit.intercept_pa * (factor[fit.modulus_unit] ?? 1e-6);
  }
}

interface PlotlyPoint {
  x: number | string;
  curveNumber: number;
  pointNumber: number;
}
interface PlotlySelected { points?: PlotlyPoint[]; }
interface PlotlyClick { points?: PlotlyPoint[]; }
// plotly.js-dist-min 在挂载的 div 上附加 on/removeAllListeners 等方法
interface PlotlyDiv extends HTMLDivElement {
  on(event: 'plotly_selected', cb: (event: PlotlySelected) => void): void;
  on(event: 'plotly_click', cb: (event: PlotlyClick) => void): void;
  removeAllListeners?(event: string): void;
}
