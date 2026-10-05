export interface ExcludedPoint {
  index: number;
  reason: string;
}

export interface MachineComplianceCorrection {
  enabled: boolean;
  coefficient: number | null;
  unit: string | null;
}

export interface ComplianceCorrectionResult {
  enabled: boolean;
  applied: boolean;
  coefficient: number | null;
  unit: string | null;
  coefficient_m_per_n: number | null;
  machine_displacement_rule: string | null;
  n_nonphysical: number;
  nonphysical_indices: number[];
  nonphysical_reasons: Record<string, string>;
}

export interface FitRequest {
  run_id: number;
  strain_source?: 'extensometer' | 'crosshead';
  stress_unit?: string;
  strain_min: number | null;
  strain_max: number | null;
  excluded_points: ExcludedPoint[];
  machine_compliance?: MachineComplianceCorrection | null;
}

export interface FitResult {
  slope_pa: number;
  modulus_in_output_unit: number;
  modulus_unit: string;
  intercept_pa: number;
  strain_min: number;
  strain_max: number;
  n_points: number;
  n_excluded: number;
  r_squared: number;
  rmse_pa: number;
  max_abs_residual_pa: number;
  residuals: {
    index: number; strain: number; stress: number;
    fitted_stress: number; residual: number;
  }[];
  excluded: ExcludedPoint[];
}

export interface IntervalSensitivity {
  label: string;
  strain_min: number;
  strain_max: number;
  modulus_in_output_unit: number;
  r_squared: number;
  n_points: number;
}

export interface YieldResult {
  found: boolean;
  method: string;
  offset_strain: number;
  proof_stress: number | null;
  stress_unit: string;
  at_strain: number | null;
  reason: string;
  yield_unclear: boolean;
}

export interface FractureResult {
  engineering_fracture_strain: number | null;
  elongation_percent: number | null;
  reduction_of_area_percent: number | null;
  ultimate_tensile_strength: number;
  stress_unit: string;
  strain_at_uts: number;
  data_complete: boolean;
  missing_inputs: string[];
}

export interface CurvePoint {
  index: number;
  strain: number;
  engineering_stress: number | null;
  true_stress: number | null;
  true_stress_valid: boolean;
  load_n: number;
  source: string;
  uncorrected_strain: number | null;
  physically_valid: boolean;
  nonphysical_reason: string | null;
}

export interface AnalysisResult {
  run_id: number;
  strain_source_used: string;
  stress_unit: string;
  elastic_fit: FitResult;
  interval_sensitivity: IntervalSensitivity[];
  yield_result: YieldResult;
  fracture: FractureResult;
  compliance_correction: ComplianceCorrectionResult;
  curve: CurvePoint[];
  necking_index: number | null;
  warnings: string[];
  provenance: Record<string, unknown>;
}

export interface RunDetail {
  run_id: number;
  specimen_code: string;
  equipment: Record<string, number | string>;
  calc_plan: { strain_source: string; stress_unit: string };
  channels: { kind: string; unit: string; point_count: number }[];
  raw_values: Record<string, number[]>;
}

export interface Report {
  report_id: number;
  run_id: number;
  title: string;
  markdown: string;
  generated_at: string;
}
