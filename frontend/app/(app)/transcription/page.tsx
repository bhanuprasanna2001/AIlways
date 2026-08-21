import { Suspense } from "react";
import TranscriptionContent from "@/components/transcription/transcription-content";
import { Spinner } from "@/components/ui/spinner";

export default function TranscriptionPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-64 items-center justify-center">
          <Spinner className="h-6 w-6" />
        </div>
      }
    >
      <TranscriptionContent />
    </Suspense>
  );
}
