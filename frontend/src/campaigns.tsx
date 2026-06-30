import type { Challenge, SeriesData } from "./api";

export type CampaignModule = {
  key: string;
  label: string;
  eyebrow: string;
  tone: string;
  classifyChallenge: (challenge: Challenge) => string;
  intro: (series: SeriesData) => string;
};

const defaultModule: CampaignModule = {
  key: "default",
  label: "Hypogeum",
  eyebrow: "Arena Series",
  tone: "Enter the arena, read the brief, and prove the solve.",
  classifyChallenge: (challenge) => challenge.category,
  intro: (series) => series.description,
};

const biafraModule: CampaignModule = {
  key: "biafra",
  label: "Biafra Campaign",
  eyebrow: "Recovered Archive",
  tone: "A dossier of intercepted signals, field reports, altered invoices, binaries, and hidden messages.",
  classifyChallenge: (challenge) => {
    const text = `${challenge.title} ${challenge.category} ${challenge.description}`.toLowerCase();
    if (text.includes("voip") || text.includes("wire") || text.includes("rtp") || text.includes("sip")) {
      return "Signal intercept";
    }
    if (text.includes("frida") || text.includes("reverse") || text.includes("binary")) {
      return "Reverse artifact";
    }
    if (text.includes("invoice") || text.includes("image") || text.includes("forensic")) {
      return "Forensic exhibit";
    }
    if (text.includes("web") || text.includes("command") || text.includes("market")) {
      return "Live service";
    }
    return challenge.category;
  },
  intro: (series) =>
    `${series.description} Each challenge is presented as a recovered exhibit. Download the archive when available, start an instance only when the dossier calls for live access, then submit the recovered flag.`,
};

export function getCampaignModule(series?: Pick<SeriesData, "title" | "description"> | null): CampaignModule {
  const haystack = `${series?.title ?? ""} ${series?.description ?? ""}`.toLowerCase();
  if (haystack.includes("biafra")) {
    return biafraModule;
  }
  return defaultModule;
}
