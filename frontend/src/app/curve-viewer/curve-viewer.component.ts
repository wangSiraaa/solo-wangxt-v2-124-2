import {
  AfterViewInit, Component, ElementRef, OnDestroy, ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import Plotly from 'plotly.js-dist-min';

import { ApiService } from '../api.service';
import {
  AnalysisResult, ExcludedPoint, FitRequest, Report, RunDetail,
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
          this.loading = false;
          this.drawRawCurve();
        });
      },
      error: (e) => { this.error = e.message; this.loading = false; },
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

  private buildRequest(): FitRequest {
    return {
      run_id: this.run!.run_id,
      strain_source: this.strainSource,
      stress_unit: this.stressUnit,
      strain_min: this.strainMin,
      strain_max: this.strainMax,
      excluded_points: this.excluded,
    };
  }

  analyze(): void {
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
        this.error = typeof e.error?.detail === 'string'
          ? e.error.detail : JSON.stringify(e.error);
      },
    });
  }

  generateReport(): void {
    if (!this.run) { return; }
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

    Plotly.react(this.plotEl.nativeElement, [
      { x: engX, y: engY, mode: 'lines', name: '工程应力-应变',
        line: { color: '#2563eb' } },
      { x: trueX, y: trueY, mode: 'lines', name: '真实应力（颈缩后不换算）',
        line: { color: '#7c3aed' } },
      { x: exclX, y: exclY, mode: 'markers', name: '人工排除点（有原因）',
        marker: { color: '#dc2626', size: 8, symbol: 'x' } },
      fitLine, offsetLine,
    ], {
      title: `应力-应变（应变来源：${r.strain_source_used}）— 红竖线颈缩起点`,
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
