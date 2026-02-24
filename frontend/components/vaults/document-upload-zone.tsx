"use client";

import { useCallback, useState } from "react";
import { useDropzone } from "react-dropzone";
import { Upload, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { apiFetch, ApiError } from "@/lib/api";
import {
  ALLOWED_FILE_TYPES,
  MAX_FILE_SIZE_BYTES,
  MAX_FILE_SIZE_MB,
} from "@/lib/constants";
import { cn, getFileExtension, formatFileSize } from "@/lib/utils";

type UploadItem = {
  file: File;
  status: "pending" | "uploading" | "success" | "error";
  error?: string;
};

type Props = {
  vaultId: string;
  onUploadComplete: () => void;
};

export function DocumentUploadZone({ vaultId, onUploadComplete }: Props) {
  const [uploads, setUploads] = useState<UploadItem[]>([]);

  const onDrop = useCallback(
    async (acceptedFiles: File[]) => {
      // Build item list with client-side validation
      const items: UploadItem[] = acceptedFiles.map((file) => {
        const ext = getFileExtension(file.name);
        if (
          !ALLOWED_FILE_TYPES.includes(ext as (typeof ALLOWED_FILE_TYPES)[number])
        ) {
          return {
            file,
            status: "error" as const,
            error: `Unsupported type: .${ext}`,
          };
        }
        if (file.size > MAX_FILE_SIZE_BYTES) {
          return {
            file,
            status: "error" as const,
            error: `File exceeds ${MAX_FILE_SIZE_MB}MB limit`,
          };
        }
        if (file.size === 0) {
          return { file, status: "error" as const, error: "File is empty" };
        }
        return { file, status: "pending" as const };
      });

      setUploads(items);

      // Upload valid files sequentially
      for (let i = 0; i < items.length; i++) {
        if (items[i].status !== "pending") continue;

        setUploads((prev) =>
          prev.map((item, idx) =>
            idx === i ? { ...item, status: "uploading" } : item,
          ),
        );

        try {
          const formData = new FormData();
          formData.append("file", items[i].file);

          await apiFetch(`/api/vaults/${vaultId}/documents/upload`, {
            method: "POST",
            body: formData,
          });

          setUploads((prev) =>
            prev.map((item, idx) =>
              idx === i ? { ...item, status: "success" } : item,
            ),
          );
        } catch (err) {
          const message =
            err instanceof ApiError ? err.detail : "Upload failed";
          setUploads((prev) =>
            prev.map((item, idx) =>
              idx === i ? { ...item, status: "error", error: message } : item,
            ),
          );
        }
      }

      onUploadComplete();
    },
    [vaultId, onUploadComplete],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
      "text/plain": [".txt"],
      "text/markdown": [".md"],
    },
    maxSize: MAX_FILE_SIZE_BYTES,
  });

  const clearUploads = () => setUploads([]);

  const allDone = uploads.every(
    (u) => u.status === "success" || u.status === "error",
  );

  return (
    <div className="space-y-3">
      <div
        {...getRootProps()}
        className={cn(
          "cursor-pointer rounded-xl border-2 border-dashed p-8 text-center transition-colors",
          isDragActive
            ? "border-foreground/50 bg-neutral-50 dark:bg-neutral-800/30"
            : "border-neutral-300 hover:border-neutral-400 dark:border-neutral-700 dark:hover:border-neutral-600",
        )}
      >
        <input {...getInputProps()} />
        <Upload className="mx-auto h-8 w-8 text-neutral-400" />
        <p className="mt-2 text-sm font-medium text-foreground">
          {isDragActive ? "Drop files here" : "Drop files here or click to upload"}
        </p>
        <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
          PDF, TXT, MD · Max {MAX_FILE_SIZE_MB}MB
        </p>
      </div>

      {/* Upload progress list */}
      {uploads.length > 0 && (
        <div className="space-y-2">
          {uploads.map((item, i) => (
            <div
              key={`${item.file.name}-${i}`}
              className="flex items-center gap-3 rounded-lg border border-neutral-200 px-3 py-2 dark:border-neutral-700"
            >
              {item.status === "uploading" && (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-neutral-500" />
              )}
              {item.status === "success" && (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
              )}
              {item.status === "error" && (
                <AlertCircle className="h-4 w-4 shrink-0 text-red-500" />
              )}
              {item.status === "pending" && (
                <div className="h-4 w-4 shrink-0" />
              )}

              <span className="flex-1 truncate text-sm text-foreground">
                {item.file.name}
              </span>
              <span className="text-xs text-neutral-400">
                {formatFileSize(item.file.size)}
              </span>

              {item.status === "error" && (
                <span className="max-w-[200px] truncate text-xs text-red-500">
                  {item.error}
                </span>
              )}
            </div>
          ))}

          {allDone && uploads.length > 0 && (
            <button
              onClick={clearUploads}
              className="text-xs text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300"
            >
              Clear
            </button>
          )}
        </div>
      )}
    </div>
  );
}
