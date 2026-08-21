import { Suspense } from "react";
import SessionsContent from "@/components/sessions/sessions-content";
import { Spinner } from "@/components/ui/spinner";

export default function SessionsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-64 items-center justify-center">
          <Spinner className="h-6 w-6" />
        </div>
      }
    >
      <SessionsContent />
    </Suspense>
  );
}
