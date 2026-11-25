import { NovaShopStorefront } from "@/components/NovaShopStorefront";
import type { StoreRuntimeConfig } from "@/lib/types";

export const dynamic = "force-dynamic";

export default function HomePage() {
  const initialConfig: StoreRuntimeConfig = {
    demoApiUrl: process.env.NEXT_PUBLIC_DEMO_API_URL || "http://localhost:8001",
    flagsmithEnvKey: process.env.NEXT_PUBLIC_FLAGSMITH_ENV_KEY || "",
    flagsmithEdgeApiUrl:
      process.env.NEXT_PUBLIC_FLAGSMITH_EDGE_API_URL ||
      process.env.FLAGSMITH_EDGE_API_URL ||
      "https://edge.api.flagsmith.com/api/v1",
    releaseGuardUrl: process.env.NEXT_PUBLIC_RELEASEGUARD_URL || "http://localhost:3000",
  };

  return <NovaShopStorefront initialConfig={initialConfig} />;
}
