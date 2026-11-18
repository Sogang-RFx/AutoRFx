"use client";
/**
 * The files of the open RFQ package, provided by the app shell so that every
 * citation can say which file it points into (A, B, … in package order) without
 * each screen loading the document again.
 */
import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import type { PackageFile } from "./contract";

const PackageContext = createContext<PackageFile[]>([]);

export function PackageProvider({ files, children }: { files: PackageFile[]; children: ReactNode }) {
  return <PackageContext.Provider value={files}>{children}</PackageContext.Provider>;
}

export function usePackageFiles(): PackageFile[] {
  return useContext(PackageContext);
}

/** Short, stable file tag: A, B, C … in package order; the raw id when the file is unknown. */
export function fileTag(files: PackageFile[], id: string): string {
  const index = files.findIndex((f) => f.id === id);
  return index >= 0 && index < 26 ? String.fromCharCode(65 + index) : id;
}

export function fileName(files: PackageFile[], id: string): string {
  return files.find((f) => f.id === id)?.name ?? id;
}
