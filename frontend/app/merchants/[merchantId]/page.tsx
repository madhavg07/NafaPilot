import { Suspense } from "react";

import { MerchantDashboard } from "@/components/merchant/MerchantDashboard";
import { Loading } from "@/components/ui";

async function Content({ params }: { params: PageProps<"/merchants/[merchantId]">["params"] }) {
  const { merchantId } = await params;
  return <MerchantDashboard merchantId={decodeURIComponent(merchantId)} />;
}

export default function Page(props: PageProps<"/merchants/[merchantId]">) {
  return (
    <Suspense fallback={<Loading what="merchant" />}>
      <Content params={props.params} />
    </Suspense>
  );
}
