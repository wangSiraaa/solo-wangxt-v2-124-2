import {
  AfterViewInit, Component, ElementRef, OnDestroy, ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import Plotly from 'plotly.js-dist-min';

import { ApiService } from '../api.service';
import {
  AnalysisResult, ComplianceUnit, DemoCreated, ExcludedPoint, FitRequest,
  MachineCompliance, Report, RunDetail,
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
  strainMin: number | null = null;
  strainMax: number | null = null;
  excluded: ExcludedPoint[] = [];
  excludeReason = '';
  pendingExcludeIndex: number | null = null;

  // 机器柔度修正（可选；仅夹具位移应变可用）
  complianceEnabled = false;
  complianceCoefficient: number | null = null;
  complianceUnit: ComplianceUnit = 'mm/kN';
  readonly complianceUnits: ComplianceUnit[] = ['m/N', 'mm/N', 'mm/kN'];

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
        const point = this.result?.curve[p.pointNumber];
        if (point && point.physically_valid === false) {
          this.error = `索引 #${point.index} 是柔度修正后的非物理点（${point.invalid_reason ?? '原因未知'}），`
            + '已被系统标记且不参与拟合；如确认数据无误可人工排除，但必须填写原因';
          return;
        }
        this.error = '';
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
      next: (d: DemoCreated) => {
        this.api.getRun(d.run_id).subscribe((run) => {
          this.run = run;
          this.strainSource = run.calc_plan.strain_source as 'extensometer' | 'crosshead';
          this.stressUnit = run.calc_plan.stress_unit;
          this.strainMin = null;
          this.strainMax = null;
          // 预填合成案例随数据提供的校准值（实验员仍可改成自己的证书值）
          if (d.machine_compliance) {
            this.complianceCoefficient = d.machine_compliance.coefficient;
            this.complianceUnit = d.machine_compliance.unit;
          } else {
            this.complianceCoefficient = null;
          }
          this.complianceEnabled = false;
          this.loading = false;
          this.drawRawCurve();
        });
      },
      error: (e) => { this.error = e.message; this.loading = false; },
    });
  }

  onStrainSourceChange(): void {
    // 柔度修正只对夹具位移有物理意义；切到引伸计时自动关闭修正
    if (this.strainSource !== 'crosshead') {
      this.complianceEnabled = false;
    }
  }

  get complianceApplicable(): boolean {
    return this.strainSource === 'crosshead';
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

  private buildCompliance(): MachineCompliance | null {
    if (!this.complianceEnabled || this.strainSource !== 'crosshead') {
      return null;
    }
    return {
      coefficient: Number(this.complianceCoefficient),
      unit: this.complianceUnit,
    };
  }

  private buildRequest(): FitRequest {
    return {
      run_id: this.run!.run_id,
      strain_source: this.strainSource,
      stress_unit: this.stressUnit,
      strain_min: this.strainMin,
      strain_max: this.strainMax,
      excluded_points: this.excluded,
      machine_compliance: this.buildCompliance(),
    };
  }

  private validateCompliance(): string | null {
    if (!this.complianceEnabled || this.strainSource !== 'crosshead') {
      return null;
    }
    const c = Number(this.complianceCoefficient);
    if (this.complianceCoefficient === null || Number.isNaN(c)) {
      return '已启用机器柔度修正，但未填写柔度系数：请输入校准值，或取消勾选以关闭修正';
    }
    if (c <= 0) {
      return `柔度系数必须为正（收到 ${this.complianceCoefficient}），请核对校准证书`;
    }
    if (!this.complianceUnit) {
      return '已启用机器柔度修正，但未选择校准单位（m/N、mm/N 或 mm/kN）：缺少单位的系数无法解释';
    }
    return null;
  }

  analyze(): void {
    this.error = '';
    const complianceError = this.validateCompliance();
    if (complianceError) {
      this.error = complianceError;
      return;
    }
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
        this.error = typeof e.error?.detail === 'string'
          ? e.error.detail : JSON.stringify(e.error);
      },
    });
  }

  generateReport(): void {
    if (!this.run) { return; }
    const complianceError = this.validateCompliance();
    if (complianceError) {
      this.error = complianceError;
      return;
    }
    this.loading = true;
    this.api.report(this.run.run_id, this.buildRequest()).subscribe({
      next: (r) => { this.report = r; this.loading = false; },
      error: (e) => {
        this.loading = false;
        this.error = typeof e.error?.detail === 'string'
          ? e.error.detail : JSON.stringify(e.error);
      },
    });
  }

  // ---- 绘图 ----

  private drawRawCurve(): void {
    if (!this.run) { return; }
    const raw = this.run.raw_values;
    const le = Number(this.run.equipment['extensometer_gauge_length_mm'] ?? 50) * 1e-3;
    // 前端预览不做面积换算，仅显示载荷-位移两个独立通道，避免任何混用暗示
    Plotly.react(this.plotEl.nativeElement, [
      {
        x: (raw['extensometer_displacement'] ?? raw['crosshead_displacement']),
        y: raw['load'],
        mode: 'lines', name: '载荷-位移（原始通道，单位见元数据）',
        line: { color: '#2563eb' },
      },
    ], {
      title: '原始信号预览：载荷 vs 位移（通道分列）',
      xaxis: { title: '位移 (m，原始 SI 值)' },
      yaxis: { title: '载荷 (N)' },
      dragmode: 'select',
      selectdirection: 'h',
      shapes: [],
    }, { responsive: true, displaylogo: false });
    void le;
  }

  private drawResult(r: AnalysisResult): void {
    const pts = r.curve;
    const excludedSet = new Set(r.elastic_fit.excluded.map((p) => p.index));
    const isCorrected = r.compliance_correction !== null;
    // 修正曲线上非物理点（负修正位移/回退）：断开显示，不参与拟合
    const nonphysicalSet = new Set(
      pts.filter((p) => p.physically_valid === false).map((p) => p.index));
    const engX = pts.map((p) =>
      (nonphysicalSet.has(p.index) || excludedSet.has(p.index)) ? null : p.strain);
    const engY = pts.map((p) =>
      (nonphysicalSet.has(p.index) || excludedSet.has(p.index)) ? null : p.engineering_stress);
    const truePts = pts.filter(
      (p) => p.true_stress_valid && p.physically_valid !== false);
    const trueX = truePts.map((p) => p.strain);
    const trueY = truePts.map((p) => p.true_stress);
    const exclX = pts.filter((p) => excludedSet.has(p.index)).map((p) => p.strain);
    const exclY = pts.filter((p) => excludedSet.has(p.index)).map((p) => p.engineering_stress);
    const npPts = pts.filter((p) => nonphysicalSet.has(p.index));
    const npX = npPts.map((p) => p.strain);
    const npY = npPts.map((p) => p.engineering_stress);

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
    if (r.necking_index !== null && pts[r.necking_index]) {
      shapes.push({
        type: 'line', xref: 'x', yref: 'paper',
        x0: pts[r.necking_index].strain, x1: pts[r.necking_index].strain,
        y0: 0, y1: 1, line: { color: '#b91c1c', dash: 'dot', width: 1 },
      });
    }

    const traces: Record<string, unknown>[] = [
      { x: engX, y: engY, mode: 'lines',
        name: isCorrected ? '工程应力-应变（柔度修正后）' : '工程应力-应变',
        line: { color: '#2563eb', width: 2 } },
    ];
    // 未修正的原始夹具位移对照曲线：修正启用时同时显示，明示“原始信号未改动”
    if (isCorrected && r.reference_curve) {
      traces.push({
        x: r.reference_curve.map((p) => p.strain),
        y: r.reference_curve.map((p) => p.engineering_stress),
        mode: 'lines', name: '原始夹具位移曲线（未修正，仅对照）',
        line: { color: '#9ca3af', dash: 'dot', width: 1.5 },
        opacity: 0.9,
      });
    }
    traces.push(
      { x: trueX, y: trueY, mode: 'lines', name: '真实应力（颈缩后不换算）',
        line: { color: '#7c3aed' } },
      { x: npX, y: npY, mode: 'markers',
        name: `修正后非物理点（${nonphysicalSet.size}，不参与拟合）`,
        marker: { color: '#dc2626', size: 10, symbol: 'star-triangle-down' } },
      { x: exclX, y: exclY, mode: 'markers', name: '人工排除点（有原因）',
        marker: { color: '#f59e0b', size: 8, symbol: 'x' } },
      fitLine, offsetLine,
    );

    const titleMode = isCorrected
      ? `（柔度修正后：C=${r.compliance_correction!.coefficient} ${r.compliance_correction!.unit}）`
      : `（应变来源：${r.strain_source_used}）`;
    Plotly.react(this.plotEl.nativeElement, traces, {
      title: `应力-应变 ${titleMode} — 红竖线颈缩起点`,
      xaxis: { title: '工程应变 ε (mm/mm)' },
      yaxis: { title: `应力 (${r.stress_unit})` },
      shapes,
      dragmode: 'select',
      selectdirection: 'h',
      hovermode: 'closest',
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
