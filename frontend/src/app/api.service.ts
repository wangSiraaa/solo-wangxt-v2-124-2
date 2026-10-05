import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { AnalysisResult, DemoCreated, FitRequest, Report, RunDetail } from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  constructor(private http: HttpClient) {}

  createDemo(caseName: string, omitDiameter = false): Observable<DemoCreated> {
    const q = omitDiameter ? '?omit_diameter=true' : '';
    return this.http.post<DemoCreated>(
      `/api/demo/synthetic/${caseName}${q}`, {});
  }

  getRun(runId: number): Observable<RunDetail> {
    return this.http.get<RunDetail>(`/api/runs/${runId}`);
  }

  analyze(req: FitRequest): Observable<AnalysisResult> {
    return this.http.post<AnalysisResult>('/api/runs/analyze', req);
  }

  report(runId: number, req: FitRequest): Observable<Report> {
    return this.http.post<Report>(`/api/runs/${runId}/report`, req);
  }
}
