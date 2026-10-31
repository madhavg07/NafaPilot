import { Suspense } from "react";

import { ExperimentResults } from "@/components/experiment/ExperimentResults";
import { Loading } from "@/components/ui";

async function Content({ params }: { params: PageProps<"/experiments/[experimentId]">["params"] }) {
  const { experimentId } = await params;
  return <ExperimentResults experimentId={experimentId} />;
}

export default function Page(props: PageProps<"/experiments/[experimentId]">) {
  return (
    <Suspense fallback={<Loading what="experiment" />}>
      <Content params={props.params} />
    </Suspense>
  );
}
