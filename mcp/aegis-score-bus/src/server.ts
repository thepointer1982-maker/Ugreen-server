import { McpServer } from "@modelcontextprotocol/server";
import { serveStdio } from "@modelcontextprotocol/server/stdio";
import * as z from "zod/v4";
import { createHash } from "node:crypto";
import { readFile, readdir, stat } from "node:fs/promises";
import { basename, extname, join, relative, resolve, sep } from "node:path";

const EVIDENCE_CLASSES = [
  "REAL_DEVICE_MEASUREMENT",
  "VERIFIED_EXPORT",
  "TEST_FIXTURE",
  "SYNTHETIC",
  "HEURISTIC",
  "PROJECTED_TARGET",
  "TEMPLATE",
  "ARCHIVE_CLAIM",
  "UNKNOWN"
] as const;

type EvidenceClass = (typeof EVIDENCE_CLASSES)[number];

const roots = {
  ugreen: resolve(process.env.AEGIS_UGREEN_REPO ?? "/srv/aegis/Ugreen-server"),
  openjarvis: resolve(process.env.AEGIS_OPENJARVIS_REPO ?? "/srv/aegis/OpenJarvis-main"),
  drive: resolve(process.env.AEGIS_DRIVE_HUB ?? "/srv/aegis/00_KI-Agenten_Datenhub")
};

const expected = {
  ugreen: [
    "scores/latest.json",
    "scores/deepdiag.json",
    "scores/history.jsonl",
    "scores/network-score.json",
    "scores/export-session.json",
    "scores/manifest.sha256",
    "dashboard/autocheck.json"
  ],
  openjarvis: [
    "aegis/quality/quality-gates.v1.json",
    "aegis/a1-field-kit/manifest.v1.1.1.json",
    "scores/real-a1-score-latest.json",
    "scores/a3-decision-latest.json"
  ],
  drive: [
    "02_Index",
    "03_Projekte",
    "06_Logs_Exports"
  ]
};

function assertInside(root: string, candidate: string): string {
  const abs = resolve(root, candidate);
  if (abs !== root && !abs.startsWith(root + sep)) {
    throw new Error("Path escapes configured evidence root");
  }
  return abs;
}

async function exists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch {
    return false;
  }
}

async function sha256(path: string): Promise<string> {
  const data = await readFile(path);
  return createHash("sha256").update(data).digest("hex");
}

async function fileMeta(root: string, rel: string) {
  const path = assertInside(root, rel);
  if (!(await exists(path))) {
    return { path: rel, exists: false };
  }
  const s = await stat(path);
  if (!s.isFile()) {
    return {
      path: rel,
      exists: true,
      kind: "directory",
      modified_at: s.mtime.toISOString()
    };
  }
  return {
    path: rel,
    exists: true,
    kind: "file",
    bytes: s.size,
    modified_at: s.mtime.toISOString(),
    sha256: await sha256(path)
  };
}

function classify(value: unknown, file: string): EvidenceClass {
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const direct = obj.evidence_class ?? obj.evidenceClass ?? obj.evidence;
    if (typeof direct === "string" && EVIDENCE_CLASSES.includes(direct as EvidenceClass)) {
      return direct as EvidenceClass;
    }
  }
  const lower = file.toLowerCase();
  if (lower.includes("fixture") || lower.includes("selftest") || lower.includes("test")) return "TEST_FIXTURE";
  if (lower.includes("template") || lower.includes("example")) return "TEMPLATE";
  if (lower.includes("projected") || lower.includes("target")) return "PROJECTED_TARGET";
  if (lower.includes("synthetic")) return "SYNTHETIC";
  if (lower.includes("archive")) return "ARCHIVE_CLAIM";
  return "UNKNOWN";
}

function extractScores(value: unknown, source: string, out: Array<Record<string, unknown>>, prefix = ""): void {
  if (Array.isArray(value)) {
    value.forEach((v, i) => extractScores(v, source, out, prefix ? `${prefix}[${i}]` : `[${i}]`));
    return;
  }
  if (!value || typeof value !== "object") return;

  const obj = value as Record<string, unknown>;
  const evidenceClass = classify(value, source);
  for (const [key, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key;
    const scoreLike = /(score|health|stability|autonomy|confidence|learning|recovery|failover|a1|a3|status)$/i.test(key);
    if (scoreLike && (typeof v === "number" || typeof v === "string" || typeof v === "boolean")) {
      out.push({
        name: path,
        value: v,
        source,
        evidence_class: evidenceClass
      });
    }
    if (v && typeof v === "object") extractScores(v, source, out, path);
  }
}

async function walkJson(root: string, relRoot: string, maxFiles: number): Promise<string[]> {
  const start = assertInside(root, relRoot);
  if (!(await exists(start))) return [];
  const found: string[] = [];
  async function walk(abs: string): Promise<void> {
    if (found.length >= maxFiles) return;
    for (const ent of await readdir(abs, { withFileTypes: true })) {
      if (found.length >= maxFiles) return;
      const p = join(abs, ent.name);
      if (ent.isDirectory()) {
        await walk(p);
      } else if ([".json", ".jsonl"].includes(extname(ent.name).toLowerCase())) {
        found.push(relative(root, p));
      }
    }
  }
  const s = await stat(start);
  if (s.isDirectory()) await walk(start);
  else found.push(relRoot);
  return found;
}

async function verifyManifest(root: string, relManifest: string) {
  const manifestPath = assertInside(root, relManifest);
  const raw = await readFile(manifestPath, "utf8");
  const results: Array<Record<string, unknown>> = [];
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const m = /^([a-fA-F0-9]{64})\s+[* ]?(.+)$/.exec(trimmed);
    if (!m) {
      results.push({ line: trimmed, valid: false, reason: "unparsed" });
      continue;
    }
    const [, expectedHash, rel] = m;
    const target = assertInside(root, rel.trim());
    if (!(await exists(target))) {
      results.push({ file: rel.trim(), valid: false, reason: "missing" });
      continue;
    }
    const actual = await sha256(target);
    results.push({
      file: rel.trim(),
      expected_sha256: expectedHash.toLowerCase(),
      actual_sha256: actual,
      valid: actual === expectedHash.toLowerCase()
    });
  }
  return results;
}

serveStdio(() => {
  const server = new McpServer({
    name: "aegis-score-bus",
    version: "0.1.0"
  });

  server.registerTool(
    "aegis_sources_status",
    {
      description: "Read-only status of configured AEGIS evidence sources and expected canonical files.",
      inputSchema: z.object({})
    },
    async () => {
      const result: Record<string, unknown> = {};
      for (const [name, root] of Object.entries(roots)) {
        const checks = [];
        for (const rel of expected[name as keyof typeof expected]) {
          checks.push(await fileMeta(root, rel));
        }
        result[name] = { root, checks };
      }
      return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.registerTool(
    "aegis_score_snapshot",
    {
      description: "Extract score-like values from local AEGIS JSON/JSONL evidence without promoting UNKNOWN/test/projected data to real evidence.",
      inputSchema: z.object({
        source: z.enum(["ugreen", "openjarvis", "drive"]),
        subpath: z.string().default("."),
        maxFiles: z.number().int().min(1).max(500).default(200)
      })
    },
    async ({ source, subpath, maxFiles }) => {
      const root = roots[source];
      const files = await walkJson(root, subpath, maxFiles);
      const scores: Array<Record<string, unknown>> = [];
      const parseErrors: Array<Record<string, unknown>> = [];

      for (const rel of files) {
        const path = assertInside(root, rel);
        try {
          const raw = await readFile(path, "utf8");
          if (rel.endsWith(".jsonl")) {
            raw.split(/\r?\n/).filter(Boolean).forEach((line, i) => {
              try {
                const parsed = JSON.parse(line);
                extractScores(parsed, `${rel}#L${i + 1}`, scores);
              } catch {
                parseErrors.push({ source: rel, line: i + 1, error: "invalid-jsonl-record" });
              }
            });
          } else {
            extractScores(JSON.parse(raw), rel, scores);
          }
        } catch (error) {
          parseErrors.push({ source: rel, error: String(error) });
        }
      }

      const payload = {
        generated_at: new Date().toISOString(),
        source,
        root,
        files_scanned: files.length,
        scores,
        parse_errors: parseErrors,
        rule: "Only REAL_DEVICE_MEASUREMENT and VERIFIED_EXPORT may be treated as real operational evidence."
      };
      return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
    }
  );

  server.registerTool(
    "aegis_verify_manifest",
    {
      description: "Verify a SHA-256 manifest against files under one configured AEGIS source root.",
      inputSchema: z.object({
        source: z.enum(["ugreen", "openjarvis", "drive"]),
        manifest: z.string()
      })
    },
    async ({ source, manifest }) => {
      const checks = await verifyManifest(roots[source], manifest);
      const payload = {
        source,
        manifest,
        verified: checks.length > 0 && checks.every((x) => x.valid === true),
        checks
      };
      return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
    }
  );

  server.registerTool(
    "aegis_evidence_read",
    {
      description: "Read one text/JSON evidence file from a configured root. Path traversal is blocked. Binary files are rejected.",
      inputSchema: z.object({
        source: z.enum(["ugreen", "openjarvis", "drive"]),
        path: z.string(),
        maxBytes: z.number().int().min(1).max(1048576).default(262144)
      })
    },
    async ({ source, path, maxBytes }) => {
      const root = roots[source];
      const abs = assertInside(root, path);
      const s = await stat(abs);
      if (!s.isFile()) throw new Error("Requested path is not a file");
      if (s.size > maxBytes) throw new Error(`File exceeds maxBytes (${s.size} > ${maxBytes})`);
      const ext = extname(path).toLowerCase();
      if (![".json", ".jsonl", ".md", ".txt", ".sha256", ".log", ".yaml", ".yml"].includes(ext)) {
        throw new Error("Binary or unsupported evidence type");
      }
      const text = await readFile(abs, "utf8");
      const payload = {
        source,
        path,
        basename: basename(path),
        bytes: s.size,
        modified_at: s.mtime.toISOString(),
        sha256: await sha256(abs),
        content: text
      };
      return { content: [{ type: "text", text: JSON.stringify(payload, null, 2) }] };
    }
  );

  return server;
});
