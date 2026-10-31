import { Suspense } from "react";

import { CampaignReview } from "@/components/campaign/CampaignReview";
import { Loading } from "@/components/ui";

async function Content({ params }: { params: PageProps<"/campaigns/[campaignId]">["params"] }) {
  const { campaignId } = await params;
  return <CampaignReview campaignId={campaignId} />;
}

export default function Page(props: PageProps<"/campaigns/[campaignId]">) {
  return (
    <Suspense fallback={<Loading what="campaign" />}>
      <Content params={props.params} />
    </Suspense>
  );
}
