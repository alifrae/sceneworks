/**
 * Remove only SceneWorks-generated E2E projects from the backend database.
 *
 * The workflow suite creates temporary Git repositories and historically only
 * removed those directories, leaving Project/Task/Execution rows in the normal
 * development database. Playwright runs this module both before and after the
 * suite. A killed run may still leave rows temporarily, but the next run removes
 * them before executing any tests.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010";

type ProjectRecord = {
  id: number;
  name: string;
  description: string;
  repository_path: string;
};

function isGeneratedE2EProject(project: ProjectRecord): boolean {
  return (
    project.name.startsWith("e2e-") &&
    project.description === "E2E test project" &&
    project.repository_path.toLowerCase().includes("sceneworks-e2e-")
  );
}

export default async function cleanupGeneratedProjects(): Promise<void> {
  const response = await fetch(`${API_URL}/api/projects?limit=500`);
  if (!response.ok) {
    throw new Error(`E2E cleanup could not list projects: ${response.status} ${await response.text()}`);
  }

  const projects = (await response.json()) as ProjectRecord[];
  const generated = projects.filter(isGeneratedE2EProject);
  for (const project of generated) {
    const deleted = await fetch(
      `${API_URL}/api/projects/${project.id}?purge_history=true&force=true`,
      { method: "DELETE" },
    );
    if (!deleted.ok) {
      throw new Error(
        `E2E cleanup could not delete project ${project.id} (${project.name}): ` +
          `${deleted.status} ${await deleted.text()}`,
      );
    }
  }

  if (generated.length > 0) {
    console.log(`Removed ${generated.length} generated SceneWorks E2E project(s).`);
  }
}
