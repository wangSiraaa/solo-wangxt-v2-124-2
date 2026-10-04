import { Component } from '@angular/core';
import { CurveViewerComponent } from './curve-viewer/curve-viewer.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CurveViewerComponent],
  template: `<app-curve-viewer></app-curve-viewer>`,
})
export class AppComponent {}
