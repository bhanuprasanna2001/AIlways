"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FileText, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/types";

/**
 * Renders a single citation/evidence card with expandable quote.
 * Shared across Copilot chat, live transcription claims, and session detail.
 */
export function CitationCard({
  citation,
  index,
}: {
  citation: Citation;
  index: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const isLong = citation.quote.length > 200;

  return (
    <div className="overflow-hidden rounded-lg border border-neutral-200 transition-colors hover:border-neutral-300 dark:border-neutral-700 dark:hover:border-neutral-600">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <FileText className="h-3.5 w-3.5 shrink-0 text-neutral-400" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
          {citation.doc_title}
          {citation.section && (
            <span className="text-neutral-400"> · {citation.section}</span>
          )}
          {citation.page && (
            <span className="text-neutral-400"> · p.{citation.page}</span>
          )}
        </span>
        <span className="shrink-0 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] font-mono text-neutral-500 dark:bg-neutral-800">
          [{index + 1}]
        </span>
        {isLong &&
          (expanded ? (
            <ChevronDown className="h-3 w-3 shrink-0 text-neutral-400" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0 text-neutral-400" />
          ))}
      </button>
      <div
        className={cn(
          "overflow-hidden border-t border-neutral-100 px-3 py-2 dark:border-neutral-800",
          isLong && !expanded && "max-h-24",
        )}
      >
        <div className="prose prose-xs max-w-none overflow-x-auto text-xs text-neutral-600 dark:prose-invert dark:text-neutral-400 prose-p:my-1 prose-table:my-1 prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1 prose-table:text-xs prose-table:border prose-th:border prose-td:border prose-th:bg-neutral-50 dark:prose-th:bg-neutral-800/50">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {citation.quote}
          </ReactMarkdown>
        </div>
        {isLong && !expanded && (
          <div className="relative -mt-6 h-6 bg-gradient-to-t from-white to-transparent dark:from-neutral-900" />
        )}
      </div>
    </div>
  );
}
