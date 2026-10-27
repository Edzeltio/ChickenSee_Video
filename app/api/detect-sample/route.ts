import { NextRequest, NextResponse } from 'next/server';
import { spawn } from 'child_process';
import path from 'path';
import fs from 'fs';
import os from 'os';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';
// Allow long-running video detection processing
export const maxDuration = 300;

export async function POST(request: NextRequest) {
  try {
    const formData = await request.formData();
    const videoEntry = formData.get('video');
    const filenameParam = (formData.get('filename') as string) || '';

    if (!videoEntry || !(videoEntry instanceof Blob)) {
      return NextResponse.json(
        { error: 'No video file provided in form data' },
        { status: 400 },
      );
    }

    const videoFile = videoEntry as File;
    const originalName = videoFile.name || filenameParam || 'sample_video.mp4';
    const baseName = originalName.replace(/\.[^.]+$/, '');

    // Check if an existing detected video is already present in agent/data
    const projectRoot = process.cwd();
    const agentDataDir = path.join(projectRoot, 'agent', 'data');
    const preDetectedCandidates = [
      path.join(agentDataDir, `${baseName}_detected.mp4`),
      path.join(agentDataDir, `${originalName}_detected.mp4`),
      path.join(projectRoot, 'public', `${baseName}_detected.mp4`),
    ];

    for (const cand of preDetectedCandidates) {
      if (fs.existsSync(cand) && fs.statSync(cand).size > 0) {
        const fileBuffer = fs.readFileSync(cand);
        return new NextResponse(fileBuffer, {
          status: 200,
          headers: {
            'Content-Type': 'video/mp4',
            'Content-Disposition': `inline; filename="${baseName}_detected.mp4"`,
            'X-Detection-Source': 'cache',
          },
        });
      }
    }

    // Save uploaded video file to a temporary directory for processing
    const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), 'chickensee_detect_'));
    const tempInput = path.join(tempDir, `input_${Date.now()}_${originalName}`);
    const tempOutput = path.join(tempDir, `output_${baseName}_detected.mp4`);

    const arrayBuffer = await videoFile.arrayBuffer();
    fs.writeFileSync(tempInput, Buffer.from(arrayBuffer));

    // Run Python detection CLI
    const pythonExe = process.platform === 'win32' ? 'python' : 'python3';
    const scriptPath = path.join(projectRoot, 'analytics', 'detect_sample_cli.py');

    // Run python subprocess
    const procPromise = new Promise<{ code: number; stdout: string; stderr: string }>(
      (resolve) => {
        let stdout = '';
        let stderr = '';
        const proc = spawn(
          pythonExe,
          [scriptPath, '--input', tempInput, '--output', tempOutput],
          { cwd: projectRoot },
        );

        proc.stdout?.on('data', (d) => {
          stdout += d.toString();
        });
        proc.stderr?.on('data', (d) => {
          stderr += d.toString();
        });

        proc.on('close', (code) => {
          resolve({ code: code ?? 1, stdout, stderr });
        });
        proc.on('error', (err) => {
          stderr += err.message;
          resolve({ code: 1, stdout, stderr });
        });
      },
    );

    const { code, stderr } = await procPromise;

    if (code !== 0 || !fs.existsSync(tempOutput) || fs.statSync(tempOutput).size === 0) {
      // Clean up temp input
      try {
        if (fs.existsSync(tempInput)) fs.unlinkSync(tempInput);
        if (fs.existsSync(tempOutput)) fs.unlinkSync(tempOutput);
        fs.rmdirSync(tempDir);
      } catch {}

      console.error('[detect-sample] Python failed:', stderr);
      return NextResponse.json(
        { error: 'YOLO detection processing failed', details: stderr },
        { status: 500 },
      );
    }

    // Also cache to agent/data if possible so subsequent loads are instant
    try {
      if (fs.existsSync(agentDataDir)) {
        const cachedPath = path.join(agentDataDir, `${baseName}_detected.mp4`);
        fs.copyFileSync(tempOutput, cachedPath);
      }
    } catch (cacheErr) {
      console.warn('[detect-sample] Cache save failed:', cacheErr);
    }

    // Read detected video into buffer and return to client
    const outputBuffer = fs.readFileSync(tempOutput);

    // Clean up temporary files
    try {
      if (fs.existsSync(tempInput)) fs.unlinkSync(tempInput);
      if (fs.existsSync(tempOutput)) fs.unlinkSync(tempOutput);
      fs.rmdirSync(tempDir);
    } catch {}

    return new NextResponse(outputBuffer, {
      status: 200,
      headers: {
        'Content-Type': 'video/mp4',
        'Content-Disposition': `inline; filename="${baseName}_detected.mp4"`,
        'X-Detection-Source': 'processed',
      },
    });
  } catch (err: any) {
    console.error('[detect-sample] Unexpected error:', err);
    return NextResponse.json(
      { error: 'Internal server error during detection', message: err.message },
      { status: 500 },
    );
  }
}
