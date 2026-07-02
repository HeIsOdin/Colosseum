import { z } from "zod";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

function parseJsonObject(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export const UserDetailsSchema = z.object({
  pid: z.string(),
  sids: z.array(z.number()).default([]),
  is_admin: z.boolean().default(false),
});

export type UserDetails = z.infer<typeof UserDetailsSchema>;

export const MetadataSchema = z.preprocess(
  parseJsonObject,
  z.record(z.string(), z.unknown()).default({}),
);

export type SeriesMetadata = z.infer<typeof MetadataSchema>;

export const SeriesHostSchema = z.preprocess(
  parseJsonObject,
  z.object({
    name: z.string().default("Colosseum"),
    url: z.string().nullable().optional(),
  }).passthrough(),
);

export type SeriesHost = z.infer<typeof SeriesHostSchema>;

export const SeriesSummarySchema = z.object({
  sid: z.number(),
  title: z.string(),
  description: z.string(),
  starts_at: z.string(),
  ends_at: z.string().nullable().optional(),
  image: z.string().nullable().optional(),
});

export type SeriesSummary = z.infer<typeof SeriesSummarySchema>;

export const SeriesOverviewPayloadSchema = z.object({
  title: z.string(),
  description: z.string(),
  host: SeriesHostSchema,
  starts_at: z.string(),
  ends_at: z.string().nullable().optional(),
  image: z.string().nullable().optional(),
  metadata: MetadataSchema,
});

export type SeriesOverview = z.infer<typeof SeriesOverviewPayloadSchema> & { sid: number };

export const SolverSchema = z.object({
  display_name: z.string().nullable().optional(),
  avatar: z.string().nullable().optional(),
  solved_at: z.string(),
});

export const ChallengeSchema = z.object({
  cid: z.number(),
  title: z.string(),
  description: z.string(),
  author: z.string().nullable().optional(),
  points: z.number(),
  category: z.string(),
  difficulty: z.string(),
  prerequisite: z.number().nullable().optional(),
  requires_instance: z.boolean().default(false),
  file_url: z.string().nullable().optional(),
  solvers: z.array(SolverSchema).default([]),
});

export type Challenge = z.infer<typeof ChallengeSchema>;

export const SeriesDataSchema = SeriesSummarySchema.extend({
  challenges: z.array(ChallengeSchema).default([]),
});

export type SeriesData = z.infer<typeof SeriesDataSchema>;

export const PlayerSolveSchema = z.object({
  sid: z.number(),
  cid: z.number(),
  points: z.number(),
  solved_at: z.string(),
  category: z.string().nullable().optional(),
  difficulty: z.string().nullable().optional(),
});

export const PlayerSchema = z.object({
  pid: z.string(),
  display_name: z.string(),
  avatar: z.string(),
  solves: z.array(PlayerSolveSchema).default([]),
});

export type Player = z.infer<typeof PlayerSchema>;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);

  if (!(init.body instanceof FormData) && init.body !== undefined) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const message =
      payload && typeof payload === "object" && "message" in payload
        ? String((payload as { message: unknown }).message)
        : `Request failed with status ${response.status}`;
    throw new ApiError(message, response.status, payload);
  }

  return payload as T;
}

function normalizeUserPayload(payload: unknown): UserDetails {
  const raw = payload as Record<string, unknown> | null;
  const details = raw?.details ?? raw;
  return UserDetailsSchema.parse(details);
}

export const api = {
  async identify(): Promise<UserDetails> {
    const payload = await request<unknown>("/auth/");
    return normalizeUserPayload(payload);
  },

  async login(email: string, password: string): Promise<UserDetails> {
    const payload = await request<unknown>("/auth/", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    return normalizeUserPayload(payload);
  },

  async register(email: string, password: string): Promise<{ success: boolean; message: string }> {
    return request("/auth/", {
      method: "PUT",
      body: JSON.stringify({ email, password }),
    });
  },

  async logout(): Promise<void> {
    await request("/auth/", { method: "DELETE" });
  },

  async listSeries(): Promise<SeriesSummary[]> {
    const payload = await request<{ series: unknown[] }>("/series/?limit=20");
    return z.array(SeriesSummarySchema).parse(payload.series ?? []);
  },

  async getSeriesOverview(sid: number): Promise<SeriesOverview> {
    const payload = await request<{ overview: unknown }>(`/series/${sid}/overview/`);
    const overview = SeriesOverviewPayloadSchema.parse(payload.overview);
    return { ...overview, sid };
  },

  async getSeries(sid: number): Promise<SeriesData> {
    const payload = await request<{ series: unknown }>(`/series/${sid}`);
    return SeriesDataSchema.parse(payload.series);
  },

  async joinSeries(sid: number): Promise<void> {
    await request(`/series/${sid}/join`, { method: "PUT" });
  },

  async leaveSeries(sid: number): Promise<void> {
    await request(`/series/${sid}/leave`, { method: "DELETE" });
  },

  async submitFlag(sid: number, cid: number, flag: string): Promise<{ success: boolean; message: string }> {
    return request(`/series/${sid}/challenges/${cid}`, {
      method: "POST",
      body: JSON.stringify({ flag }),
    });
  },

  async controlInstance(sid: number, cid: number, action: "start" | "stop" | "restart") {
    return request(`/series/${sid}/challenges/${cid}`, {
      method: "PATCH",
      body: JSON.stringify({ action }),
    });
  },

  async getPlayer(pid: string): Promise<Player> {
    const payload = await request<{ player: unknown }>(`/players/${pid}/`);
    return PlayerSchema.parse(payload.player);
  },

  async createSeries(input: {
    title: string;
    description: string;
    host: {
      name: string;
      url?: string;
    };
    starts_at: string;
    ends_at?: string;
    image: string;
    metadata: Record<string, unknown>;
  }) {
    return request("/series/", {
      method: "PUT",
      body: JSON.stringify(input),
    });
  },

  async createChallenge(
    sid: number,
    input: {
      title: string;
      description: string;
      author: string;
      points: number;
      category: string;
      difficulty: string;
      flag: string;
      prerequisite?: number | null;
      requires_instance?: boolean;
      file_url?: string;
    },
  ) {
    return request(`/series/${sid}/challenges/`, {
      method: "PUT",
      body: JSON.stringify(input),
    });
  },
};
