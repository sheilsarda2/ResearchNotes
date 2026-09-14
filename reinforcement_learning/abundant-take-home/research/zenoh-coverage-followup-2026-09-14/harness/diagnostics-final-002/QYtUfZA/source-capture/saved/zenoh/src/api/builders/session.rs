//
// Copyright (c) 2024 ZettaScale Technology
//
// This program and the accompanying materials are made available under the
// terms of the Eclipse Public License 2.0 which is available at
// http://www.eclipse.org/legal/epl-2.0, or the Apache License, Version 2.0
// which is available at https://www.apache.org/licenses/LICENSE-2.0.
//
// SPDX-License-Identifier: EPL-2.0 OR Apache-2.0
//
// Contributors:
//   ZettaScale Zenoh Team, <zenoh@zettascale.tech>
//

#[cfg(any(feature = "shared-memory", feature = "unstable"))]
use std::sync::Arc;
use std::{
    fmt,
    future::{IntoFuture, Ready},
};

use zenoh_core::{Resolvable, Wait};
#[cfg(feature = "internal")]
use zenoh_keyexpr::OwnedKeyExpr;
use zenoh_result::ZResult;
#[cfg(feature = "shared-memory")]
use zenoh_shm::api::client_storage::ShmClientStorage;

#[cfg(feature = "unstable")]
use crate::api::timestamp_stack::TimestampContext;
use crate::api::{session::Session, timestamp_stack::TimestampCallback};
#[cfg(feature = "internal")]
use crate::net::runtime::DynamicRuntime;

/// A builder returned by [`crate::open`] used to open a zenoh [`Session`].
///
/// # Examples
/// ```
/// # #[tokio::main]
/// # async fn main() {
///
/// let session = zenoh::open(zenoh::Config::default()).await.unwrap();
/// # }
/// ```
#[must_use = "Resolvables do nothing unless you resolve them using `.await` or `zenoh::Wait::wait`"]
pub struct OpenBuilder<TryIntoConfig>
where
    TryIntoConfig: std::convert::TryInto<crate::config::Config> + Send + 'static,
    <TryIntoConfig as std::convert::TryInto<crate::config::Config>>::Error: std::fmt::Debug,
{
    config: TryIntoConfig,
    #[cfg(feature = "shared-memory")]
    shm_clients: Option<Arc<ShmClientStorage>>,
    timestamp_callback: Option<TimestampCallback>,
}

impl<TryIntoConfig> fmt::Debug for OpenBuilder<TryIntoConfig>
where
    TryIntoConfig: std::convert::TryInto<crate::config::Config> + Send + 'static,
    <TryIntoConfig as std::convert::TryInto<crate::config::Config>>::Error: std::fmt::Debug,
{
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut debug = f.debug_struct("OpenBuilder");
        debug.field("config", &"..");
        #[cfg(feature = "shared-memory")]
        debug.field("shm_clients", &self.shm_clients.as_ref().map(|_| ".."));
        debug.field(
            "timestamp_callback",
            &self.timestamp_callback.as_ref().map(|_| ".."),
        );
        debug.finish()
    }
}

impl<TryIntoConfig> OpenBuilder<TryIntoConfig>
where
    TryIntoConfig: std::convert::TryInto<crate::config::Config> + Send + 'static,
    <TryIntoConfig as std::convert::TryInto<crate::config::Config>>::Error: std::fmt::Debug,
{
    pub(crate) fn new(config: TryIntoConfig) -> Self {
        Self {
            config,
            #[cfg(feature = "shared-memory")]
            shm_clients: None,
            timestamp_callback: None,
        }
    }

    /// Sets a callback producing custom timestamps for the timestamp instrumentation.
    ///
    /// The callback produces the timestamps recorded by this session in the
    /// [`TimestampStack`](crate::timestamp_stack::TimestampStack) of the instrumented
    /// messages it sends, routes or receives.
    ///
    /// By default, records hold a timestamp taken from the hybrid logical clock of the session.
    /// With a callback, every record produced by this session (at any
    /// [`InterceptionPoint`](crate::timestamp_stack::InterceptionPoint)) holds the bytes returned
    /// by the callback instead, in an application defined format. When the callback returns an
    /// empty vector, no record is produced.
    ///
    /// # Examples
    /// ```
    /// # #[tokio::main]
    /// # async fn main() {
    /// let session = zenoh::open(zenoh::Config::default())
    ///     .with_timestamp_callback(|ctx| {
    ///         let now = std::time::SystemTime::now()
    ///             .duration_since(std::time::UNIX_EPOCH)
    ///             .unwrap();
    ///         let mut bytes = now.as_nanos().to_le_bytes().to_vec();
    ///         bytes.extend_from_slice(&ctx.zid.to_le_bytes());
    ///         bytes
    ///     })
    ///     .await
    ///     .unwrap();
    /// # }
    /// ```
    #[zenoh_macros::unstable]
    pub fn with_timestamp_callback<F>(mut self, callback: F) -> Self
    where
        F: Fn(TimestampContext) -> Vec<u8> + Send + Sync + 'static,
    {
        self.timestamp_callback = Some(Arc::new(callback));
        self
    }
}

#[cfg(feature = "shared-memory")]
impl<TryIntoConfig> OpenBuilder<TryIntoConfig>
where
    TryIntoConfig: std::convert::TryInto<crate::config::Config> + Send + 'static,
    <TryIntoConfig as std::convert::TryInto<crate::config::Config>>::Error: std::fmt::Debug,
{
    pub fn with_shm_clients(mut self, shm_clients: Arc<ShmClientStorage>) -> Self {
        self.shm_clients = Some(shm_clients);
        self
    }
}

impl<TryIntoConfig> Resolvable for OpenBuilder<TryIntoConfig>
where
    TryIntoConfig: std::convert::TryInto<crate::config::Config> + Send + 'static,
    <TryIntoConfig as std::convert::TryInto<crate::config::Config>>::Error: std::fmt::Debug,
{
    type To = ZResult<Session>;
}

impl<TryIntoConfig> Wait for OpenBuilder<TryIntoConfig>
where
    TryIntoConfig: std::convert::TryInto<crate::config::Config> + Send + 'static,
    <TryIntoConfig as std::convert::TryInto<crate::config::Config>>::Error: std::fmt::Debug,
{
    fn wait(self) -> <Self as Resolvable>::To {
        let config: crate::config::Config = self
            .config
            .try_into()
            .map_err(|e| zerror!("Invalid Zenoh configuration {:?}", &e))?;
        Session::new(
            config,
            #[cfg(feature = "shared-memory")]
            self.shm_clients,
            self.timestamp_callback,
        )
        .wait()
    }
}

impl<TryIntoConfig> IntoFuture for OpenBuilder<TryIntoConfig>
where
    TryIntoConfig: std::convert::TryInto<crate::config::Config> + Send + 'static,
    <TryIntoConfig as std::convert::TryInto<crate::config::Config>>::Error: std::fmt::Debug,
{
    type Output = <Self as Resolvable>::To;
    type IntoFuture = Ready<<Self as Resolvable>::To>;

    fn into_future(self) -> Self::IntoFuture {
        std::future::ready(self.wait())
    }
}

/// Initialize a Session with an existing Runtime.
/// This operation is used by the plugins to share the same Runtime as the router.
#[zenoh_macros::internal]
pub fn init(runtime: DynamicRuntime) -> InitBuilder {
    InitBuilder {
        runtime,
        aggregated_subscribers: vec![],
        aggregated_publishers: vec![],
    }
}

/// A builder returned by [`init`] and used to initialize a Session with an existing Runtime.
#[must_use = "Resolvables do nothing unless you resolve them using `.await` or `zenoh::Wait::wait`"]
#[doc(hidden)]
#[zenoh_macros::internal]
pub struct InitBuilder {
    runtime: DynamicRuntime,
    aggregated_subscribers: Vec<OwnedKeyExpr>,
    aggregated_publishers: Vec<OwnedKeyExpr>,
}

#[zenoh_macros::internal]
impl fmt::Debug for InitBuilder {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("InitBuilder")
            .field("runtime", &"..")
            .field("aggregated_subscribers", &self.aggregated_subscribers)
            .field("aggregated_publishers", &self.aggregated_publishers)
            .finish()
    }
}

#[zenoh_macros::internal]
impl InitBuilder {
    #[inline]
    pub fn aggregated_subscribers(mut self, exprs: Vec<OwnedKeyExpr>) -> Self {
        self.aggregated_subscribers = exprs;
        self
    }

    #[inline]
    pub fn aggregated_publishers(mut self, exprs: Vec<OwnedKeyExpr>) -> Self {
        self.aggregated_publishers = exprs;
        self
    }
}

#[zenoh_macros::internal]
impl Resolvable for InitBuilder {
    type To = ZResult<Session>;
}

#[zenoh_macros::internal]
impl Wait for InitBuilder {
    fn wait(self) -> <Self as Resolvable>::To {
        Ok(Session::init(
            self.runtime.into(),
            self.aggregated_subscribers,
            self.aggregated_publishers,
        )
        .wait())
    }
}

#[zenoh_macros::internal]
impl IntoFuture for InitBuilder {
    type Output = <Self as Resolvable>::To;
    type IntoFuture = Ready<<Self as Resolvable>::To>;

    fn into_future(self) -> Self::IntoFuture {
        std::future::ready(self.wait())
    }
}
