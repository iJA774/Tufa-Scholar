"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import WorkspacePage from "../workspace/WorkspacePage";

export default function SettingsPage() {
  const router = useRouter();
  const closeSettings = useCallback(() => router.replace("/workspace"), [router]);

  return <WorkspacePage initialSettingsOpen onSettingsClose={closeSettings} />;
}
