use std::time::{Duration, Instant};
use zenoh::{Config, Session, Wait, query::Querier};
type E=Box<dyn std::error::Error+Send+Sync>;
fn cfg(mode:&str)->Config{let mut c=Config::default();c.insert_json5("mode",&format!("\"{mode}\"")).unwrap();c.insert_json5("scouting/multicast/enabled","false").unwrap();if mode=="router"{c.insert_json5("listen/endpoints",r#"["tcp/127.0.0.1:17447"]"#).unwrap();}else{c.insert_json5("connect/endpoints",r#"["tcp/127.0.0.1:17447"]"#).unwrap();}c}
async fn open()->Session{zenoh::open(cfg("client")).await.unwrap()}
async fn check(label:&str,q:&Querier<'_>)->usize{let started=Instant::now();let rs=q.get().payload("ping").await.unwrap();let mut n=0;while let Ok(r)=rs.recv_async().await{if r.result().is_ok(){n+=1;}}println!("{label} replies={n} elapsed_us={}",started.elapsed().as_micros());n}
#[tokio::main(flavor="multi_thread",worker_threads=2)]
async fn main()->Result<(),E>{
 let router=zenoh::open(cfg("router")).await?;
 let requester=open().await;
 for iteration in 0..5{
  let key=format!("probe/{iteration}");
  let responder=open().await;
  let qabl=responder.declare_queryable(key.clone()).callback(|q|{q.reply(q.key_expr().clone(),"pong").wait().unwrap();}).await?;
  tokio::time::sleep(Duration::from_secs(1)).await;
  let q=requester.declare_querier(key.clone()).timeout(Duration::from_secs(10)).await?;
  check(&format!("{iteration}/first-declaration"),&q).await;
  tokio::time::sleep(Duration::from_millis(250)).await;
  check(&format!("{iteration}/settled-before-disconnect"),&q).await;
  drop(q);drop(qabl);responder.close().await?;
  tokio::time::sleep(Duration::from_millis(500)).await;
  let responder=open().await;
  let qabl=responder.declare_queryable(key.clone()).callback(|q|{q.reply(q.key_expr().clone(),"pong").wait().unwrap();}).await?;
  tokio::time::sleep(Duration::from_secs(3)).await;
  let q=requester.declare_querier(key.clone()).timeout(Duration::from_secs(10)).await?;
  check(&format!("{iteration}/after-reconnect-immediate"),&q).await;
  tokio::time::sleep(Duration::from_secs(1)).await;
  check(&format!("{iteration}/after-reconnect-settled"),&q).await;
  drop(q);
  tokio::time::sleep(Duration::from_millis(500)).await;
  let q=requester.declare_querier(key.clone()).timeout(Duration::from_secs(10)).await?;
  check(&format!("{iteration}/redeclare-without-disconnect-immediate"),&q).await;
  tokio::time::sleep(Duration::from_millis(250)).await;
  check(&format!("{iteration}/redeclare-without-disconnect-settled"),&q).await;
  drop(q);drop(qabl);responder.close().await?;
 }
 requester.close().await?;router.close().await?;Ok(())
}
