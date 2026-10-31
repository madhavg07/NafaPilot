import { Suspense } from "react";

import { RunReview } from "@/components/recommendation/RunReview";
import { Loading } from "@/components/ui";

async function Content({ params }: { params: PageProps<"/recommendations/[runId]">["params"] }) {
  const { runId } = await params;
  return <RunReview runId={runId} />;
}

export default function Page(props: PageProps<"/recommendations/[runId]">) {
  return (
    <Suspense fallback={<Loading what="recommendation" />}>
      <Content params={props.params} />
    </Suspense>
  );
}
