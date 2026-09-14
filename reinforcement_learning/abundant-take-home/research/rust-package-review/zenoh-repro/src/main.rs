use std::time::Duration;
use zenoh::{Config, query::{ConsolidationMode, QueryTarget}};
type E = Box<dyn std::error::Error + Send + Sync>;
fn cfg(mode: &str) -> Config {
 let mut c=Config::default(); c.insert_json5("mode", &format!("\"{mode}\"")).unwrap();
 c.insert_json5("scouting/multicast/enabled", "false").unwrap(); c
}
#[tokio::main(flavor="multi_thread", worker_threads=2)]
async fn main() -> Result<(),E> {
 if std::env::args().nth(1).as_deref()==Some("open") {
  let mut c=cfg("client");
  c.insert_json5("connect/endpoints", "[\"tcp/127.0.0.1:17448\"]")?;
  c.insert_json5("connect/timeout_ms", "2000")?;
  c.insert_json5("connect/exit_on_failure", "false")?;
  let start=std::time::Instant::now();
  let r=tokio::time::timeout(Duration::from_millis(100), async {zenoh::open(c).await}).await;
  println!("open elapsed_ms={} timeout={}", start.elapsed().as_millis(), r.is_err()); return Ok(());
 }
 let mut c=cfg("router"); c.insert_json5("listen/endpoints", "[\"tcp/127.0.0.1:17447\"]")?;
 let router=zenoh::open(c).await?;
 for (label,complete_flags) in [("single",vec![true]),("both_complete",vec![true,true]),("mixed",vec![true,false]),("reverse_mixed",vec![false,true])] {
  let key=format!("audit/{label}"); let mut qs=Vec::new();
  for (idx,complete) in complete_flags.into_iter().enumerate() {
   qs.push(router.declare_queryable(key.clone()).complete(complete).callback(move |q|{q.reply(q.key_expr().clone(),format!("{idx}")).wait().unwrap();}).await?);
  }
  let mut c=cfg("client");c.insert_json5("connect/endpoints", "[\"tcp/127.0.0.1:17447\"]")?;let client=zenoh::open(c).await?;
  tokio::time::sleep(Duration::from_millis(250)).await;
  for target in [QueryTarget::All,QueryTarget::AllComplete] {
   let rs=client.get(key.clone()).target(target).consolidation(ConsolidationMode::None).timeout(Duration::from_secs(1)).await?;
   let mut n=0;while let Ok(r)=rs.recv_async().await {if r.result().is_ok(){n+=1;}}
   println!("{label} target={target:?} replies={n}");
  }
  client.close().await?; drop(qs);
 }
 router.close().await?;Ok(())
}
use zenoh::Wait;
