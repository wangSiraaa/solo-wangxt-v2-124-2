export interface ExcludedPoint {
  index: number;
  reason: string;
}

export type ComplianceUnit = 'm/N' | 'mm/N' | 'mm/kN';

export interface MachineCompliance {
  coefficient: number;
  unit: ComplianceUnit;
}

export interface FitRequest {
  run_id: number;
  strain_source?: 'extensometer' | 'crosshead';
  stress_unit?: string;
  strain_min: number | null;
  strain_max: number | null;
  excluded_points: ExcludedPoint[];
  machine_compliance?: MachineCompliance | null;
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
  corrected?: boolean;
  physically_valid?: boolean;
  invalid_reason?: string | null;
  machine_deformation_m?: number | null;
}

export interface ComplianceCorrectionInfo {
  coefficient: number;
  unit: string;
  coefficient_si_m_per_n: number;
  correction_formula: string;
  n_nonphysical_points: number;
  nonphysical_indices: number[];
  nonphysical_reasons: string[];
}

export interface AnalysisResult {
  run_id: number;
  strain_source_used: string;
  stress_unit: string;
  elastic_fit: FitResult;
  interval_sensitivity: IntervalSensitivity[];
  yield_result: YieldResult;
  fracture: FractureResult;
  curve: CurvePoint[];
  necking_index: number | null;
  warnings: string[];
  provenance: Record<string, unknown>;
  compliance_correction: ComplianceCorrectionInfo | null;
  reference_curve: CurvePoint[] | null;
}

export interface RunDetail {
  run_id: number;
  specimen_code: string;
  equipment: Record<string, number | string>;
  calc_plan: { strain_source: string; stress_unit: string };
  channels: { kind: string; unit: string; point_count: number }[];
  raw_values: Record<string, number[]>;
}

export interface DemoCreated {
  run_id: number;
  specimen_id: number;
  case: string;
  points: number;
  diameter_omitted: boolean;
  machine_compliance?: {
    coefficient: number;
    unit: ComplianceUnit;
    coefficient_si_m_per_n: number;
  };
}

export interface Report {
  report_id: number;
  run_id: number;
  title: string;
  markdown: string;
  generated_at: string;
}
