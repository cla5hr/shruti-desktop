import { useQuery } from "@tanstack/react-query";
import { api, type MeetingDetail } from "./client";

const PROCESSING = new Set(["pending", "processing"]);

export function useMeetings() {
  return useQuery({
    queryKey: ["meetings"],
    queryFn: api.meetings,
    refetchInterval: (query) =>
      query.state.data?.some((m) => PROCESSING.has(m.status)) ? 3000 : false,
  });
}

export function useAppSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 30_000 });
}

export function useMeeting(id: string | undefined) {
  return useQuery({
    queryKey: ["meeting", id],
    queryFn: () => api.meeting(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const m = query.state.data as MeetingDetail | undefined;
      return m && PROCESSING.has(m.status) ? 2000 : false;
    },
  });
}

export function useTranscript(id: string | undefined, ready: boolean) {
  return useQuery({
    queryKey: ["transcript", id],
    queryFn: () => api.transcript(id!),
    enabled: !!id && ready,
  });
}

export function usePeaks(id: string | undefined, hasPeaks: boolean) {
  return useQuery({
    queryKey: ["peaks", id],
    queryFn: () => api.peaks(id!),
    enabled: !!id && hasPeaks,
    staleTime: Infinity,
  });
}

export function useSpeakers(id: string | undefined, ready: boolean) {
  return useQuery({
    queryKey: ["speakers", id],
    queryFn: () => api.speakers(id!),
    enabled: !!id && ready,
  });
}

export function useJobs(id: string | undefined, active: boolean) {
  return useQuery({
    queryKey: ["jobs", id],
    queryFn: () => api.jobs(id!),
    enabled: !!id && active,
    refetchInterval: active ? 2000 : false,
  });
}
